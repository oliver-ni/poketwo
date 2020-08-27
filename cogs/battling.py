import asyncio
import math
import random
import typing
from enum import Enum
from bot import ClusterBot
import discord
from discord.ext import commands

from helpers import checks, constants, converters, pagination

from .database import Database


def setup(bot: ClusterBot):
    bot.add_cog(Battling(bot))


def get_priority(action, selected):
    if action["type"] == "move":
        return action["value"].priority * 1e20 + selected.spd

    return 1e99


class Stage(Enum):
    SELECT = 1
    PROGRESS = 2
    END = 3


class Trainer:
    def __init__(self, user: discord.Member, bot: ClusterBot):
        self.user = user
        self.pokemon = []
        self.selected_idx = 0
        self.done = False
        self.bot = bot

    @property
    def selected(self):
        if self.selected_idx == -1:
            return None
        return self.pokemon[self.selected_idx]

    async def send_selection(self):
        embed = self.bot.Embed()
        embed.title = "Waiting for opponent..." if self.done else "Choose your party"
        embed.description = (
            "Choose **3** pokémon to fight in the battle. The battle will begin once both trainers "
            "have chosen their party. "
        )

        if len(self.pokemon) > 0:
            embed.add_field(
                name="Your Party",
                value="\n".join(
                    f"{x.iv_percentage:.2%} IV {x.species} ({x.idx + 1})"
                    for x in self.pokemon
                ),
            )
        else:
            embed.add_field(name="Your Party", value="None")

        embed.add_field(name="Opponent's Party", value="???\n???\n???")

        if not self.done:
            embed.set_footer(
                text=f"Use `p!battle add <pokemon>` in this DM to add a pokémon to the party!"
            )

        await self.user.send(embed=embed)

    async def send_ready(self, opponent):
        embed = self.bot.Embed()
        embed.title = "💥 Ready to battle!"
        embed.description = "The battle will begin in 5 seconds."
        embed.add_field(
            name="Your Party",
            value="\n".join(
                f"{x.iv_percentage:.2%} IV {x.species} ({x.idx + 1})"
                for x in self.pokemon
            ),
        )
        embed.add_field(
            name="Opponent's Party",
            value="\n".join(f"{x.species}" for x in opponent.pokemon),
        )

        await self.user.send(embed=embed)

    async def get_action(self):
        embed = self.bot.Embed()
        embed.title = f"What should {self.selected.species} do?"

        actions = {}

        for idx, x in enumerate(self.selected.moves):
            actions[constants.NUMBER_REACTIONS[idx + 1]] = {
                "type": "move",
                "value": self.bot.data.move_by_number(x),
                "text": f"Use {self.bot.data.move_by_number(x).name}",
            }

        for idx, pokemon in enumerate(self.pokemon):
            if pokemon != self.selected and pokemon.hp > 0:
                actions[constants.LETTER_REACTIONS[idx]] = {
                    "type": "switch",
                    "value": idx,
                    "text": f"Switch to {pokemon.iv_percentage:.2%} {pokemon.species}",
                }

        actions["⏹️"] = {"type": "flee", "text": "Flee from the battle"}
        actions["⏭️"] = {"type": "pass", "text": "Pass this turn and do nothing."}

        # Send embed

        embed.description = "\n".join(f"{k} {v['text']}" for k, v in actions.items())
        msg = await self.user.send(embed=embed)

        async def add_reactions():
            for k in actions:
                await msg.add_reaction(k)

        asyncio.create_task(add_reactions())

        def check(react: discord.Reaction, u):
            return (
                react.message.id == msg.id
                and u.id == self.user.id
                and react.emoji in actions
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=30, check=check
            )
            action = actions[reaction.emoji]
        except asyncio.TimeoutError:
            action = {"type": "pass", "text": "nothing. Passing turn..."}

        await self.user.send(f"You selected **{action['text']}**.")

        return action


class Battle:
    def __init__(
        self, users: typing.List[discord.Member], ctx: commands.Context, manager
    ):
        self.trainers = [Trainer(x, ctx.bot) for x in users]
        self.channel = ctx.channel  # type: discord.TextChannel
        self.stage = Stage.SELECT
        self.ctx = ctx
        self.bot = ctx.bot  # type: ClusterBot
        self.manager = manager

    async def send_selection(self):
        await asyncio.gather(
            self.trainers[0].send_selection(), self.trainers[1].send_selection(),
        )

    def end(self):
        self.stage = Stage.END
        del self.manager[self.trainers[0].user]

    async def run_step(self):
        if self.stage != Stage.PROGRESS:
            return

        actions = await asyncio.gather(
            self.trainers[0].get_action(), self.trainers[1].get_action()
        )

        iterl = list(zip(actions, self.trainers, reversed(self.trainers)))

        for action, trainer, opponent in iterl:
            action["priority"] = get_priority(action, trainer.selected)

        embed = discord.Embed(color=0xF44336)
        embed.title = f"Battle between {self.trainers[0].user.display_name} and {self.trainers[1].user.display_name}."
        embed.set_footer(text="The next round will begin in 5 seconds.")

        for action, trainer, opponent in sorted(iterl, key=lambda x: x[0]["priority"]):
            if action["type"] == "flee":
                # battle's over
                await self.channel.send(
                    f"{trainer.user.mention} has fled the battle! {opponent.user.mention} has won."
                )
                self.end()
                return

            elif action["type"] == "switch":
                trainer.selected_idx = action["value"]

                embed.add_field(
                    name=f"{trainer.user.display_name} switched pokémon!",
                    value=f"{trainer.selected.species} is now on the field!",
                    inline=False,
                )

            elif action["type"] == "move":

                # calculate damage amount

                move = action["value"]

                if move.damage_class_id == 1 or move.power is None:
                    success = True
                    damage = 0
                else:
                    success = random.randint(0, 99) <= move.accuracy

                    if move.damage_class_id == 2:
                        atk = trainer.selected.atk
                        defn = opponent.selected.defn
                    else:
                        atk = trainer.selected.satk
                        defn = opponent.selected.sdef

                    damage = int(
                        (2 * trainer.selected.level / 5 + 2)
                        * move.power
                        * atk
                        / defn
                        / 50
                        + 2
                    )

                title = f"{trainer.selected.species} used {move.name}!"
                text = f"{move.name} dealt {damage} damage!"

                if success:
                    opponent.selected.hp -= damage
                else:
                    text = "Missed!"

                # check if fainted

                if opponent.selected.hp <= 0:
                    opponent.selected.hp = 0

                    text += f" {opponent.selected.species} has fainted."

                    try:
                        opponent.selected_idx = next(
                            idx for idx, x in enumerate(opponent.pokemon) if x.hp > 0
                        )

                    except StopIteration:
                        # battle's over
                        self.end()
                        opponent.selected_idx = -1
                        await self.channel.send(
                            f"{trainer.user.mention} won the battle!"
                        )
                        return

                    embed.add_field(name=title, value=text, inline=False)
                    break

                embed.add_field(name=title, value=text, inline=False)

        await self.channel.send(embed=embed)

    async def send_battle(self):
        embed = self.bot.Embed()
        embed.title = f"Battle between {self.trainers[0].user.display_name} and {self.trainers[1].user.display_name}."

        if self.stage == Stage.PROGRESS:
            embed.description = "Choose your moves in DMs. After both players have chosen, the move will be executed."
        else:
            embed.description = "The battle has ended."

        for trainer in self.trainers:
            embed.add_field(
                name=trainer.user.display_name,
                value="\n".join(
                    f"**{x.species}** • {x.hp}/{x.max_hp} HP"
                    if trainer.selected == x
                    else f"{x.species} • {x.hp}/{x.max_hp} HP"
                    for x in trainer.pokemon
                ),
            )

        await self.channel.send(embed=embed)

    async def run_battle(self):
        if self.stage != Stage.SELECT:
            return

        self.stage = Stage.PROGRESS
        while self.stage != Stage.END:
            await asyncio.sleep(5)
            await self.send_battle()
            await self.run_step()
        await self.send_battle()


class BattleManager:
    def __init__(self):
        self.battles = {}

    def __getitem__(self, user):
        return self.battles[user.id]

    def __contains__(self, user):
        return user.id in self.battles

    def __delitem__(self, user):
        for trainer in self.battles[user.id].trainers:
            del self.battles[trainer.user.id]

    def get_trainer(self, user):
        for trainer in self[user].trainers:
            if trainer.user.id == user.id:
                return trainer

    def get_opponent(self, user):
        for trainer in self[user].trainers:
            if trainer.user.id != user.id:
                return trainer

    def new(self, user1, user2, ctx):
        battle = Battle([user1, user2], ctx, self)
        self.battles[user1.id] = battle
        self.battles[user2.id] = battle
        return battle


class Battling(commands.Cog):
    """For battling."""

    def __init__(self, bot: ClusterBot):
        self.bot = bot

        if not hasattr(self.bot, "battles") or self.bot.battles is None:
            self.bot.battles = BattleManager()

    @property
    def db(self) -> Database:
        return self.bot.get_cog("Database")

    @commands.is_owner()
    @commands.command()
    async def reloadbattling(self, ctx: commands.Context):
        self.bot.battles = BattleManager()
        await ctx.send("✅| Reloaded BattleManager")

    @checks.has_started()
    @commands.group(aliases=["duel"], invoke_without_command=True)
    async def battle(self, ctx: commands.Context, *, user: discord.Member):
        """Battle another trainer with your pokémon!"""

        # Base cases

        if user == ctx.author:
            return await ctx.send("Nice try...")

        if ctx.author in self.bot.battles:
            return await ctx.send("You are already in a battle!")

        if user in self.bot.battles:
            return await ctx.send(f"**{user}** is already in a battle!")

        member = await self.bot.mongo.Member.find_one({"id": user.id})

        if member is None:
            return await ctx.send("That user hasn't picked a starter pokémon yet!")

        # Challenge to battle

        message = await ctx.send(
            f"Challenging {user.mention} to a battle. Click the checkmark to accept!"
        )
        await message.add_reaction("✅")

        def check(reaction, u):
            return (
                reaction.message.id == message.id
                and u == user
                and str(reaction.emoji) == "✅"
            )

        try:
            await self.bot.wait_for("reaction_add", timeout=30, check=check)
        except asyncio.TimeoutError:
            await message.add_reaction("❌")
            await ctx.send("The challenge has timed out.")
            return

        # Accepted, continue

        if ctx.author in self.bot.battles:
            return await ctx.send(
                "Sorry, the user who sent the challenge is already in another battle."
            )

        if user in self.bot.battles:
            return await ctx.send(
                "Sorry, you can't accept a challenge while you're already in a battle!"
            )

        battle = self.bot.battles.new(ctx.author, user, ctx)
        await battle.send_selection()

    @checks.has_started()
    @battle.command(aliases=["a"])
    async def add(
        self, ctx: commands.Context, args: commands.Greedy[converters.Pokemon]
    ):
        """Add a pokémon to a battle."""

        if ctx.author not in self.bot.battles:
            return await ctx.send("You're not in a battle!")

        updated = False

        trainer, opponent = (
            self.bot.battles.get_trainer(ctx.author),
            self.bot.battles.get_opponent(ctx.author),
        )

        for pokemon, idx in args:
            if pokemon is None:
                await ctx.send(f"{idx + 1}: Couldn't find that pokémon!")
                return

            if len(trainer.pokemon) >= 3:
                await ctx.send(
                    f"{idx + 1}: There are already enough pokémon in the party!"
                )
                return

            for x in trainer.pokemon:
                if x.idx == idx:
                    await ctx.send(f"{idx + 1}: This pokémon is already in the party!")
                    return

            pokemon.idx = idx
            pokemon.hp = pokemon.hp
            trainer.pokemon.append(pokemon)

            if len(trainer.pokemon) == 3:
                trainer.done = True

            updated = True

        if not updated:
            return

        if trainer.done and opponent.done:
            await trainer.send_ready(opponent)
            await opponent.send_ready(trainer)
            await self.bot.battles[ctx.author].run_battle()
        else:
            await trainer.send_selection()

    @checks.has_started()
    @commands.command(aliases=["mv"], rest_is_raw=True)
    async def moves(self, ctx: commands.Context, *, pokemon: converters.Pokemon):
        """View current and available moves for your pokémon."""

        pokemon, idx = pokemon

        embed = discord.Embed(color=self.bot.embed_color)
        embed.title = f"Level {pokemon.level} {pokemon.species} — Moves"
        embed.description = (
            f"Here are the moves your pokémon can learn right now. View all moves and how to get "
            f"them using `{ctx.prefix}moveset`!"
        )

        embed.add_field(
            name="Available Moves",
            value="\n".join(
                x.move.name
                for x in pokemon.species.moves
                if pokemon.level >= x.method.level
            ),
        )

        embed.add_field(
            name="Current Moves",
            value="No Moves"
            if len(pokemon.moves) == 0
            else "\n".join(self.bot.data.move_by_number(x).name for x in pokemon.moves),
        )

        await ctx.send(embed=embed)

    @checks.has_started()
    @commands.command(aliases=["l"])
    async def learn(self, ctx: commands.Context, *, search: str):
        """Learn moves for your pokémon to use in battle."""

        move = self.bot.data.move_by_name(search)

        if move is None:
            return await ctx.send("Couldn't find that move!")

        member = await self.db.fetch_member_info(ctx.author)
        pokemon = await self.db.fetch_pokemon(ctx.author, member.selected)

        if move.id in pokemon.moves:
            return await ctx.send("Your pokémon has already learned that move!")

        try:
            pokemon_move = next(
                x for x in pokemon.species.moves if x.move_id == move.id
            )
        except StopIteration:
            pokemon_move = None

        if pokemon_move is None or pokemon_move.method.level > pokemon.level:
            return await ctx.send("Your pokémon can't learn that move!")

        update = {}

        if len(pokemon.moves) >= 4:

            await ctx.send(
                "Your pokémon already knows the max number of moves! Please enter the name of a move to replace, "
                "or anything else to abort:\n "
                + "\n".join(self.bot.data.move_by_number(x).name for x in pokemon.moves)
            )

            def check(m):
                return m.channel.id == ctx.channel.id and m.author.id == ctx.author.id

            try:
                msg = await self.bot.wait_for("message", timeout=60, check=check)
            except asyncio.TimeoutError:
                return await ctx.send("Time's up. Aborted.")

            rep_move = self.bot.data.move_by_name(msg.content)

            if rep_move is None or rep_move.id not in pokemon.moves:
                return await ctx.send("Aborted.")

            idx = pokemon.moves.index(rep_move.id)

            update["$set"] = {f"pokemon.{member.selected}.moves.{idx}": move.id}

        else:
            update["$push"] = {f"pokemon.{member.selected}.moves": move.id}

        await self.db.update_member(ctx.author, update)

        return await ctx.send("Your pokémon has learned " + move.name + "!")

    @checks.has_started()
    @commands.command(aliases=["ms"], rest_is_raw=True)
    async def moveset(self, ctx: commands.Context, *, search: str):
        """View all moves for your pokémon and how to get them."""

        search = search.strip()

        if len(search) > 0 and search[0] in "Nn#" and search[1:].isdigit():
            species = self.bot.data.species_by_number(int(search[1:]))
        else:
            species = self.bot.data.species_by_name(search)

            if species is None:
                converter = converters.Pokemon(raise_errors=False)
                pokemon, idx = await converter.convert(ctx, search)
                if pokemon is not None:
                    species = pokemon.species

        if species is None:
            raise converters.PokemonConversionError(
                f"Please either enter the name of a pokémon species, nothing for your selected pokémon, a number for "
                f"a specific pokémon, `latest` for your latest pokémon. "
            )

        async def get_page(pidx, clear):
            pgstart = (pidx) * 20
            pgend = min(pgstart + 20, len(species.moves))

            # Send embed

            embed = discord.Embed(color=self.bot.embed_color)
            embed.title = f"{species} — Moveset"

            embed.set_footer(
                text=f"Showing {pgstart + 1}–{pgend} out of {len(species.moves)}."
            )

            for move in species.moves[pgstart:pgend]:
                embed.add_field(name=move.move.name, value=move.text)

            for i in range(-pgend % 3):
                embed.add_field(name="‎", value="‎")

            return embed

        paginator = pagination.Paginator(
            get_page, num_pages=math.ceil(len(species.moves) / 20)
        )
        await paginator.send(self.bot, ctx, 0)

    @commands.command(aliases=["mi"])
    async def moveinfo(self, ctx: commands.Context, *, search: str):
        """View information about a certain move."""

        move = self.bot.data.move_by_name(search)

        if move is None:
            return await ctx.send("Couldn't find a move with that name!")

        embed = discord.Embed(color=self.bot.embed_color)
        embed.title = move.name

        embed.description = move.description

        embed.add_field(name="Target", value=move.target_text, inline=False)

        for name, x in (
            ("Power", "power"),
            ("Accuracy", "accuracy"),
            ("PP", "pp"),
            ("Priority", "priority"),
            ("Type", "type"),
        ):
            if (v := getattr(move, x)) is not None:
                embed.add_field(name=name, value=v)
            else:
                embed.add_field(name=name, value="—")

        embed.add_field(name="Class", value=move.damage_class)

        await ctx.send(embed=embed)

    @checks.has_started()
    @battle.command(aliases=["x"])
    async def cancel(self, ctx: commands.Context):
        """Cancel a battle."""

        if ctx.author not in self.bot.battles:
            return await ctx.send("You're not in a battle!")

        self.bot.battles[ctx.author].end()

        await ctx.send("The battle has been canceled.")


def setup(bot: ClusterBot):
    bot.add_cog(Battling(bot))
