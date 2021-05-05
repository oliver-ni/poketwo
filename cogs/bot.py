import pickle
import random
import sys
import traceback
from datetime import datetime, timedelta
from typing import Counter

import aiohttp
import discord
from discord.channel import TextChannel
from discord.ext import commands, flags, tasks
from helpers import checks, constants, converters

GENERAL_CHANNEL_NAMES = {"welcome", "general", "lounge", "chat", "talk", "main"}


class Blacklisted(commands.CheckFailure):
    pass


class Bot(commands.Cog):
    """For basic bot operation."""

    def __init__(self, bot):
        self.bot = bot

        if not hasattr(self.bot, "prefixes"):
            self.bot.prefixes = {}

        self.post_count.start()
        self.update_status.start()
        self.process_dms.start()

        if self.bot.cluster_idx == 0 and self.bot.config.DBL_TOKEN is not None:
            self.post_dbl.start()
            self.remind_votes.start()

        self.cd = commands.CooldownMapping.from_cooldown(5, 3, commands.BucketType.user)

    async def bot_check(self, ctx):
        if ctx.invoked_with.lower() == "help":
            return True

        bucket = self.cd.get_bucket(ctx.message)
        if retry_after := bucket.update_rate_limit():
            raise commands.CommandOnCooldown(bucket, retry_after)

        return True

    async def send_dm(self, uid, content):
        priv = await self.bot.http.start_private_message(uid)
        await self.bot.http.send_message(priv["id"], content)

    @tasks.loop(seconds=0.5)
    async def process_dms(self):
        with await self.bot.redis as r:
            req = await r.blpop("send_dm")
            uid, content = pickle.loads(req[1])
            self.bot.loop.create_task(self.send_dm(uid, content))

    @process_dms.before_loop
    async def before_process_dms(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        await self.bot.process_commands(after)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        self.bot.log.info(
            f'COMMAND {ctx.author.id} {ctx.command.qualified_name}: {ctx.author} "{ctx.message.content}"'
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):

        if isinstance(error, Blacklisted):
            self.bot.log.info(f"{ctx.author.id} is blacklisted")
            return
        elif isinstance(error, commands.CommandOnCooldown):
            self.bot.log.info(f"{ctx.author.id} hit cooldown")
            await ctx.message.add_reaction("\N{HOURGLASS}")
        elif isinstance(error, commands.MaxConcurrencyReached):
            name = error.per.name
            suffix = "per %s" % name if error.per.name != "default" else "globally"
            plural = "%s times %s" if error.number > 1 else "%s time %s"
            fmt = plural % (error.number, suffix)
            await ctx.send(f"This command can only be used {fmt} at the same time.")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command cannot be used in private messages.")
        elif isinstance(error, commands.DisabledCommand):
            await ctx.send("Sorry. This command is disabled and cannot be used.")
        elif isinstance(error, commands.BotMissingPermissions):
            missing = [
                "`" + perm.replace("_", " ").replace("guild", "server").title() + "`"
                for perm in error.missing_perms
            ]
            fmt = "\n".join(missing)
            message = f"💥 Err, I need the following permissions to run this command:\n{fmt}\nPlease fix this and try again."
            botmember = (
                self.bot.user if ctx.guild is None else ctx.guild.get_member(self.bot.user.id)
            )
            if ctx.channel.permissions_for(botmember).send_messages:
                await ctx.send(message)
        elif isinstance(error, commands.ConversionError):
            await ctx.send(error.original)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send_help(ctx.command)
        elif isinstance(error, checks.Suspended):
            embed = discord.Embed(color=discord.Color.red())
            embed.title = "Account Suspended"
            embed.description = "Your account was found to be in violation of Pokétwo rules and has been permanently blacklisted from using the bot. If you wish to appeal this suspension, fill out the [appeals form](https://docs.google.com/forms/u/0/d/e/1FAIpQLSeXCWuCloEtUrwaCUwfLsFvyg-wATOZsvG4SOXEBA2adgkqvA/viewform)."
            embed.add_field(name="Reason", value=error.reason or "No reason provided")
            await ctx.send(embed=embed)
        elif isinstance(
            error,
            (
                commands.CheckFailure,
                commands.UserInputError,
                flags.ArgumentParsingError,
            ),
        ):
            await ctx.send(error)
        elif isinstance(error, commands.CommandNotFound):
            return
        else:
            print(f"Ignoring exception in command {ctx.command}")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
            print("\n\n")

    @commands.Cog.listener()
    async def on_error(self, ctx: commands.Context, error):
        if isinstance(error, discord.NotFound):
            return
        else:
            print(f"Ignoring exception in command {ctx.command}:")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
            print("\n\n")

    def sendable_channel(self, channel):
        if channel.guild.me.permissions_in(channel).send_messages:
            return channel
        return None

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        priority_channels = []
        channels = []
        for channel in guild.channels:
            if channel == guild.system_channel or any(
                x in channel.name for x in GENERAL_CHANNEL_NAMES
            ):
                priority_channels.append(channel)
            else:
                channels.append(channel)
        channels = priority_channels + channels
        try:
            channel = next(
                x
                for x in channels
                if isinstance(x, TextChannel) and guild.me.permissions_in(x).send_messages
            )
        except StopIteration:
            return
        prefix = await self.determine_prefix(guild)
        prefix = prefix[0]

        embed = discord.Embed(color=0xFE9AC9)
        embed.title = "Thanks for adding me to your server! \N{WAVING HAND SIGN}"
        embed.description = f"To get started, do `{prefix}start` to pick your starter pokémon. As server members talk, wild pokémon will automatically spawn in the server, and you'll be able to catch them with `{prefix}catch <pokémon>`! For a full command list, do `{prefix}help`."
        embed.add_field(
            name="Common Configuration Options",
            value=(
                f"• `{prefix}prefix <new prefix>` to set a different prefix (default: `p!`)\n"
                f"• `{prefix}redirect <channel>` to redirect pokémon spawns to one channel\n"
                f"• More can be found in `{prefix}config help`\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Support Server",
            value="Join our server at [discord.gg/poketwo](https://discord.gg/poketwo) for support.",
            inline=False,
        )
        await channel.send(embed=embed)

    async def determine_prefix(self, guild):
        if guild:
            if guild.id not in self.bot.prefixes:
                data = await self.bot.mongo.Guild.find_one({"id": guild.id})
                if data is None:
                    data = self.bot.mongo.Guild(id=guild.id)
                    await data.commit()

                self.bot.prefixes[guild.id] = data.prefix

            if self.bot.prefixes[guild.id] is not None:
                return [
                    self.bot.prefixes[guild.id],
                    self.bot.user.mention + " ",
                    self.bot.user.mention[:2] + "!" + self.bot.user.mention[2:] + " ",
                ]

        return [
            "p!",
            "P!",
            self.bot.user.mention + " ",
            self.bot.user.mention[:2] + "!" + self.bot.user.mention[2:] + " ",
        ]

    @commands.command()
    async def invite(self, ctx):
        """View the invite link for the bot."""

        embed = self.bot.Embed(color=0xFE9AC9)
        embed.title = "Want to add me to your server? Use the link below!"
        embed.set_thumbnail(url=self.bot.user.avatar_url)
        embed.add_field(name="Invite Bot", value="https://invite.poketwo.net/", inline=False)
        embed.add_field(name="Join Server", value="https://discord.gg/poketwo", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def donate(self, ctx):
        """Donate to receive shards."""

        await ctx.send(
            "Pokétwo relies on players like you to stay up and running. "
            "You can help support the bot by donating to receive shards, which can be used to purchase redeems and other items in the shop.\n\n"
            "**Donation Link:** https://poketwo.net/store\n\n"
        )

    async def get_stats(self):
        result = await self.bot.mongo.db.stats.aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "servers": {"$sum": "$servers"},
                        "shards": {"$sum": "$shards"},
                        "latency": {"$sum": "$latency"},
                    }
                }
            ]
        ).to_list(None)
        result = result[0]

        return result

    @tasks.loop(minutes=1)
    async def update_status(self):
        await self.bot.wait_until_ready()
        result = await self.get_stats()
        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{result['servers']:,} servers",
            )
        )

    @tasks.loop(minutes=5)
    async def post_dbl(self):
        await self.bot.wait_until_ready()
        result = await self.get_stats()
        headers = {"Authorization": self.bot.config.DBL_TOKEN}
        data = {"server_count": result["servers"], "shard_count": result["shards"]}
        async with aiohttp.ClientSession(headers=headers) as sess:
            await sess.post(f"https://top.gg/api/bots/{self.bot.user.id}/stats", data=data)

    @tasks.loop(seconds=15)
    async def remind_votes(self):
        await self.bot.wait_until_ready()
        query = {
            "need_vote_reminder": True,
            "last_voted": {"$lt": datetime.utcnow() - timedelta(hours=12)},
        }

        ids = set()

        async for x in self.bot.mongo.db.member.find(query, {"_id": 1}, no_cursor_timeout=True):
            try:
                ids.add(x["_id"])
                priv = await self.bot.http.start_private_message(x["_id"])
                await self.bot.http.send_message(
                    priv["id"],
                    "Your vote timer has refreshed. You can now vote again! https://top.gg/bot/716390085896962058/vote",
                )
            except:
                pass

        await self.bot.mongo.db.member.update_many(query, {"$set": {"need_vote_reminder": False}})
        if len(ids) > 0:
            await self.bot.redis.hdel("db:member", *[int(x) for x in ids])

    @tasks.loop(minutes=1)
    async def post_count(self):
        await self.bot.wait_until_ready()
        await self.bot.mongo.db.stats.update_one(
            {"_id": self.bot.cluster_name},
            {
                "$set": {
                    "servers": len(self.bot.guilds),
                    "shards": len(self.bot.shards),
                    "latency": min(sum(x[1] for x in self.bot.latencies), 1),
                }
            },
            upsert=True,
        )

    @commands.command(aliases=("botinfo",))
    async def stats(self, ctx):
        """View bot info."""

        result = await self.get_stats()

        embed = self.bot.Embed(color=0xFE9AC9)
        embed.title = f"Pokétwo Statistics"
        embed.set_thumbnail(url=self.bot.user.avatar_url)

        embed.add_field(name="Servers", value=result["servers"], inline=False)
        embed.add_field(name="Shards", value=result["shards"], inline=False)
        embed.add_field(
            name="Trainers",
            value=await self.bot.mongo.db.member.estimated_document_count(),
            inline=False,
        )
        embed.add_field(
            name="Average Latency",
            value=f"{int(result['latency'] * 1000 / result['shards'])} ms",
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.command()
    async def ping(self, ctx):
        """View the bot's latency."""

        message = await ctx.send("Pong!")
        ms = int((message.created_at - ctx.message.created_at).total_seconds() * 1000)

        if ms > 300 and random.random() < 0.3:
            await message.edit(
                content=(
                    f"Pong! **{ms} ms**\n\n"
                    "Tired of bot slowdowns? Running a bot is expensive, but you can help! Donate at https://poketwo.net/store."
                )
            )
        else:
            await message.edit(content=f"Pong! **{ms} ms**")

    @commands.command()
    async def start(self, ctx):
        """View the starter pokémon."""

        embed = self.bot.Embed(color=0xFE9AC9)
        embed.title = "Welcome to the world of Pokémon!"
        embed.description = f"To start, choose one of the starter pokémon using the `{ctx.prefix}pick <pokemon>` command. "

        for gen, pokemon in constants.STARTER_GENERATION.items():
            embed.add_field(name=gen, value=" · ".join(pokemon), inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def pick(self, ctx, *, name: str):
        """Pick a starter pokémon to get started."""

        member = await self.bot.mongo.fetch_member_info(ctx.author)

        if member is not None:
            return await ctx.send(
                f"You have already chosen a starter pokémon! View your pokémon with `{ctx.prefix}pokemon`."
            )

        species = self.bot.data.species_by_name(name)

        if species is None or species.name.lower() not in constants.STARTER_POKEMON:
            return await ctx.send(
                f"Please select one of the starter pokémon. To view them, type `{ctx.prefix}start`."
            )

        starter = self.bot.mongo.Pokemon.random(
            owner_id=ctx.author.id,
            species_id=species.id,
            level=1,
            xp=0,
            idx=1,
        )

        result = await self.bot.mongo.db.pokemon.insert_one(starter.to_mongo())
        await self.bot.mongo.db.member.insert_one(
            {
                "_id": ctx.author.id,
                "selected_id": result.inserted_id,
                "joined_at": datetime.utcnow(),
                "next_idx": 2,
            }
        )
        await self.bot.redis.hdel("db:member", ctx.author.id)

        await ctx.send(
            f"Congratulations on entering the world of pokémon! {species} is your first pokémon. Type `{ctx.prefix}info` to view it!"
        )

    @checks.has_started()
    @commands.command()
    async def profile(self, ctx):
        """View your profile."""

        member = await self.bot.mongo.fetch_member_info(ctx.author)

        embed = self.bot.Embed(color=0xFE9AC9)
        embed.title = "Trainer Profile"
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url)

        pokemon_caught = []
        pokemon_caught.append(
            "**Total: **" + str(await self.bot.mongo.fetch_pokedex_sum(ctx.author))
        )

        for name, filt in (
            ("Mythical", self.bot.data.list_mythical),
            ("Legendary", self.bot.data.list_legendary),
            ("Ultra Beast", self.bot.data.list_ub),
        ):
            pokemon_caught.append(
                f"**{name}: **"
                + str(
                    await self.bot.mongo.fetch_pokedex_sum(
                        ctx.author,
                        [{"$match": {"k": {"$in": [str(x) for x in filt]}}}],
                    )
                )
            )
        pokemon_caught.append("**Shiny: **" + str(member.shinies_caught))

        embed.add_field(name="Pokémon Caught", value="\n".join(pokemon_caught))
        embed.add_field(
            name="Badges",
            value=self.bot.sprites.pin_halloween if member.halloween_badge else "No badges",
        )

        await ctx.send(embed=embed)

    def cog_unload(self):
        self.post_count.cancel()
        self.update_status.cancel()

        if self.bot.cluster_idx == 0 and self.bot.config.DBL_TOKEN is not None:
            self.post_dbl.cancel()
            self.remind_votes.cancel()

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def cleanup(self, ctx, search=100):
        """Cleans up the bot's messages from the channel."""

        def check(m):
            return m.author == ctx.me or m.content.startswith(ctx.prefix)

        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
        spammers = Counter(m.author.display_name for m in deleted)
        count = len(deleted)

        messages = [f'{count} message{" was" if count == 1 else "s were"} removed.']
        if len(deleted) > 0:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"– **{author}**: {count}" for author, count in spammers)

        await ctx.send("\n".join(messages), delete_after=5)


def setup(bot):
    bot.add_cog(Bot(bot))
