from datetime import datetime

import aiohttp
import discord
from discord.ext import commands, tasks
from bot import ClusterBot
from helpers import checks, constants

from .database import Database


class Blacklisted(commands.CheckFailure):
    pass


class CommandOnCooldown(commands.CommandOnCooldown):
    pass


class Bot(commands.Cog):
    """For basic bot operation."""

    def __init__(self, bot: ClusterBot):
        self.bot = bot

        if not hasattr(self.bot, "prefixes"):
            self.bot.prefixes = {}

        self.post_count.start()
        self.post_dbl.start()

    async def bot_check(self, ctx):
        if (
            await self.bot.mongo.db.blacklist.count_documents({"_id": ctx.author.id})
            > 0
        ):
            raise Blacklisted

        return True

    @property
    def db(self) -> Database:
        return self.bot.get_cog("Database")

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
    async def invite(self, ctx: commands.Context):
        """View the invite link for the bot."""

        await ctx.send(
            "Want to add me to your server? Use the link below!\n\n"
            "Invite Bot: https://invite.poketwo.net/\n"
            "Join Server: https://discord.gg/QyEWy4C"
        )

    async def get_stats(self):
        result = await self.bot.mongo.db.stats.aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "servers": {"$sum": "$servers"},
                        "shards": {"$sum": "$shards"},
                        "users": {"$sum": "$users"},
                        "latency": {"$sum": "$latency"},
                    }
                }
            ]
        ).to_list(None)
        result = result[0]

        return result

    @tasks.loop(minutes=5)
    async def post_dbl(self):
        await self.bot.wait_until_ready()

        if self.bot.cluster_name != "Arbok":
            return

        result = await self.get_stats()

        headers = {"Authorization": self.bot.dbl_token}
        data = {"server_count": result["servers"], "shard_count": result["shards"]}
        async with aiohttp.ClientSession(headers=headers) as sess:
            r = await sess.post(
                f"https://top.gg/api/bots/{self.bot.user.id}/stats", data=data
            )

    @tasks.loop(minutes=1)
    async def post_count(self):
        await self.bot.wait_until_ready()
        await self.bot.mongo.db.stats.update_one(
            {"_id": self.bot.cluster_name},
            {
                "$set": {
                    "servers": len(self.bot.guilds),
                    "shards": len(self.bot.shards),
                    "users": sum(x.member_count for x in self.bot.guilds),
                    "latency": sum(x[1] for x in self.bot.latencies),
                }
            },
            upsert=True,
        )

    @commands.command()
    async def stats(self, ctx: commands.Context):
        """View interesting statistics about the bot."""

        result = await self.get_stats()

        embed = self.bot.Embed()
        embed.title = f"Pokétwo Statistics"
        embed.set_thumbnail(url=self.bot.user.avatar_url)

        embed.add_field(name="Servers", value=result["servers"], inline=False)
        embed.add_field(name="Shards", value=result["shards"], inline=False)
        embed.add_field(name="Users", value=result["users"], inline=False)
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
    async def ping(self, ctx: commands.Context):
        """View the bot's latency."""

        message = await ctx.send("Pong!")
        ms = (message.created_at - ctx.message.created_at).total_seconds() * 1000
        await message.edit(content=f"Pong! **{int(ms)} ms**")

    @commands.command()
    async def start(self, ctx: commands.Context):
        """View the starter pokémon."""

        embed = self.bot.Embed()
        embed.title = "Welcome to the world of Pokémon!"
        embed.description = f"To start, choose one of the starter pokémon using the `{ctx.prefix}pick <pokemon>` command. "

        for gen, pokemon in constants.STARTER_GENERATION.items():
            embed.add_field(name=gen, value=" · ".join(pokemon), inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def pick(self, ctx: commands.Context, *, name: str):
        """Pick a starter pokémon to get started."""

        member = await self.db.fetch_member_info(ctx.author)

        if member is not None:
            return await ctx.send(
                f"You have already chosen a starter pokémon! View your pokémon with `{ctx.prefix}pokemon`."
            )

        species = self.bot.data.species_by_name(name)

        if species.name.lower() not in constants.STARTER_POKEMON:
            return await ctx.send(
                f"Please select one of the starter pokémon. To view them, type `{ctx.prefix}start`."
            )

        starter = self.bot.mongo.Pokemon.random(species_id=species.id, level=1, xp=0)

        member = self.bot.mongo.Member(
            id=ctx.author.id, pokemon=[starter], selected=0, joined_at=datetime.utcnow()
        )

        await member.commit()

        await ctx.send(
            f"Congratulations on entering the world of pokémon! {species} is your first pokémon. Type `{ctx.prefix}info` to view it!"
        )

    @checks.has_started()
    @commands.command()
    async def profile(self, ctx: commands.Context):
        """View your profile."""

        embed = self.bot.Embed()
        embed.title = f"{ctx.author}"

        member = await self.db.fetch_member_info(ctx.author)

        pokemon_caught = []

        pokemon_caught.append(
            "**Total: **" + str(await self.db.fetch_pokedex_sum(ctx.author))
        )

        for name, filt in (
            ("Mythical", self.bot.data.list_mythical),
            ("Legendary", self.bot.data.list_legendary),
            ("Ultra Beast", self.bot.data.list_ub),
        ):
            pokemon_caught.append(
                f"**{name}: **"
                + str(
                    await self.db.fetch_pokedex_sum(
                        ctx.author,
                        [{"$match": {"k": {"$in": [str(x) for x in filt]}}}],
                    )
                )
            )

        pokemon_caught.append("**Shiny: **" + str(member.shinies_caught))

        embed.add_field(name="Pokémon Caught", value="\n".join(pokemon_caught))

        await ctx.send(embed=embed)

    @checks.has_started()
    @commands.command()
    async def healschema(self, ctx: commands.Context, member: discord.User = None):
        """Fix database schema if broken."""

        await self.db.update_member(
            member or ctx.author,
            {"$pull": {f"pokemon": {"species_id": {"$exists": False}}}},
        )
        await self.db.update_member(member or ctx.author, {"$pull": {f"pokemon": None}})
        await ctx.send("Trying to heal schema...")


def setup(bot: ClusterBot):
    bot.add_cog(Bot(bot))
