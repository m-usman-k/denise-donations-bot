import discord
import json
import os
import time
import asyncio
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, Any, List

# Load environment variables from .env if it exists
def load_env():
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

load_env()
TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "donations_data.json"

class DataManager:
    def __init__(self, filename: str):
        self.filename = filename
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, Exception):
                return {"guilds": {}}
        return {"guilds": {}}

    def save(self):
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def get_guild(self, guild_id: int) -> Dict[str, Any]:
        gid = str(guild_id)
        if gid not in self.data["guilds"]:
            self.data["guilds"][gid] = {
                "categories": [],
                "donations": {}, # {user_id: {category: amount}}
                "settings": {"log_channel_id": None},
                "managers": [], # [role_id]
                "autoroles": [], # [{"category": "name", "threshold": 100, "role_id": 123}]
                "active_leaderboards": {} # {category: {"channel_id": 123, "message_id": 456}}
            }
        return self.data["guilds"][gid]

class DonationBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.data_manager = DataManager(DATA_FILE)
        self.remove_command("help")

    async def setup_hook(self):
        self.add_view(LeaderboardView(None, None))
        print("Bot initialized and view added.")

    def is_manager(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator: 
            return True
        guild_data = self.data_manager.get_guild(interaction.guild.id)
        manager_roles = guild_data.get("managers", [])
        return any(role.id in manager_roles for role in interaction.user.roles)

    async def log_action(self, guild: discord.Guild, embed: discord.Embed):
        guild_data = self.data_manager.get_guild(guild.id)
        log_channel_id = guild_data["settings"].get("log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel: 
                embed.add_field(name="Date & Time", value=f"<t:{int(time.time())}:f>", inline=False)
                await channel.send(embed=embed)

    async def update_leaderboard_messages(self, guild_id: int, category: str):
        guild_data = self.data_manager.get_guild(guild_id)
        lb_info = guild_data["active_leaderboards"].get(category)

        if not lb_info:
            return

        channel_id = lb_info["channel_id"]
        message_id = lb_info["message_id"]

        # Get donation data for this category
        all_donations = guild_data["donations"]
        sorted_rows = []
        for uid, cats in all_donations.items():
            if category in cats and cats[category] > 0:
                sorted_rows.append((int(uid), cats[category]))
        
        sorted_rows.sort(key=lambda x: x[1], reverse=True)

        channel = self.get_channel(channel_id)
        if not channel: 
            return
            
        try:
            message = await channel.fetch_message(message_id)
            view = LeaderboardView(category, sorted_rows) 
            embed = await create_leaderboard_embed(category, guild_id, sorted_rows[:10])
            await message.edit(embed=embed, view=view)
        except discord.NotFound:
            # If message is gone, remove it from active leaderboards
            if category in guild_data["active_leaderboards"]:
                del guild_data["active_leaderboards"][category]
                self.data_manager.save()

bot = DonationBot()

donation = app_commands.Group(name="donation", description="Main donation commands")
don_category = app_commands.Group(name="category", parent=donation, description="Manage donation categories")
don_autorole = app_commands.Group(name="autorole", parent=donation, description="Manage donation autoroles")
don_settings = app_commands.Group(name="settings", parent=donation, description="Configure bot settings")
bot.tree.add_command(donation)

def is_manager_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        return bot.is_manager(interaction)
    return app_commands.check(predicate)

async def category_autocomplete(interaction: discord.Interaction, current: str):
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    categories = guild_data.get("categories", [])
    choices = [app_commands.Choice(name=cat, value=cat) for cat in categories if current.lower() in cat.lower()]
    return choices[:25] if choices else [app_commands.Choice(name="No categories found", value="")]

def category_exists(category: str, guild_id: int) -> bool:
    guild_data = bot.data_manager.get_guild(guild_id)
    return category in guild_data.get("categories", [])

def create_log_embed(interaction: discord.Interaction, embed: discord.Embed, auto = False) -> discord.Embed:
    if not auto:
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
    return embed

@donation.command(name="add", description="Add donations to a user")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(member="The member to add donations to", category="The category of the donation", amount="The amount of the donation")
async def add(interaction: discord.Interaction, member: discord.Member, category: str, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    uid = str(member.id)
    
    if uid not in guild_data["donations"]:
        guild_data["donations"][uid] = {}
    
    current_amount = guild_data["donations"][uid].get(category, 0)
    new_amount = current_amount + amount
    guild_data["donations"][uid][category] = new_amount
    bot.data_manager.save()

    await bot.update_leaderboard_messages(interaction.guild.id, category)
    
    roles_added = []
    autoroles = guild_data.get("autoroles", [])
    # Sort autoroles by threshold to check them in order
    sorted_autoroles = sorted([r for r in autoroles if r["category"] == category], key=lambda x: x["threshold"])
    
    for ar in sorted_autoroles:
        if new_amount >= ar["threshold"]:
            role = interaction.guild.get_role(ar["role_id"])
            if role and role not in member.roles:
                try:
                    await member.add_roles(role)
                    roles_added.append(role)
                except discord.Forbidden:
                    embed = discord.Embed(title="Error", description=f"I don't have permissions to give role {role.mention} to {member.mention}.", color=discord.Color.red())
                    await interaction.followup.send(embed=embed)

    embed = discord.Embed(title="Donation Added", description=f"✅ {amount} point{'s' if amount > 1 else ''} added to {member.mention}'s donations.", color=0x95d5ff)
    embed.add_field(name=f"Category", value=category, inline=False)
    embed.add_field(name=f"Updated Amount", value=f"**{new_amount}**", inline=False)
    if roles_added:
        embed.description += f"\n\n➕ Roles Granted:\n{', '.join(role.mention for role in roles_added)}"
    
    await interaction.followup.send(embed=embed, ephemeral=True)
    log_embed = create_log_embed(interaction, embed)
    log_embed.set_thumbnail(url=member.display_avatar.url)
    await bot.log_action(interaction.guild, log_embed)

@donation.command(name="remove", description="Remove donations from a user")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(member="The member to remove donations from", category="The category of the donation", amount="The amount of the donation")
async def remove(interaction: discord.Interaction, member: discord.Member, category: str, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    uid = str(member.id)
    
    current = 0
    if uid in guild_data["donations"]:
        current = guild_data["donations"][uid].get(category, 0)
    
    if amount > current:
        amount = current
    
    if amount <= 0:
        embed = discord.Embed(title="Error", description="No donations to remove.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    guild_data["donations"][uid][category] = current - amount
    bot.data_manager.save()
    
    await bot.update_leaderboard_messages(interaction.guild.id, category)
    
    embed = discord.Embed(title="Donation Removed", description=f"✅ {amount} point{'s' if amount > 1 else ''} removed from {member.mention}'s donations.", color=0x95d5ff)
    embed.add_field(name=f"Category", value=category, inline=False)
    embed.add_field(name=f"Updated amount", value=f"**{current - amount}**", inline=False)
    await interaction.followup.send(embed=embed)
    
    log_embed = create_log_embed(interaction, embed)
    log_embed.set_thumbnail(url=member.display_avatar.url)
    await bot.log_action(interaction.guild, log_embed)

@bot.tree.command(name="donation_check", description="View member's total donations")
@app_commands.describe(member="The user to check donations for")
async def donation_check(interaction: discord.Interaction, member: discord.Member = None):
    # If member is specified and not the caller, check if user is manager
    if member and member != interaction.user:
        if not bot.is_manager(interaction):
            embed = discord.Embed(title="Error", description="You do not have permission to check other members' donations.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    await interaction.response.defer()
    
    member = member or interaction.user
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    uid = str(member.id)
    
    data = guild_data["donations"].get(uid, {})
    
    embed = discord.Embed(title=f"Stats for {member.display_name}", color=0xc1e1ff)
    if data:
        lines = []
        for name, total in data.items():
            if total > 0:
                lines.append(f"**{name}**: {total}")
        embed.description = "\n".join(lines) if lines else "No donations recorded."
    else:
        embed.description = "No donations recorded."
    
    await interaction.followup.send(embed=embed)

class LeaderboardView(discord.ui.View):
    def __init__(self, category: str, data: list[tuple[int, int]]):
        super().__init__(timeout=None)
        self.category = category
        self.data = data or []
        self.page = 0
        self.per_page = 10
        self.max_pages = (len(self.data) - 1) // self.per_page + 1 if self.data else 1
        self.update_buttons()

    def update_buttons(self):
        self.previous_page.disabled = (self.page == 0)
        self.next_page.disabled = (self.page >= self.max_pages - 1)

    def create_description(self):
        if not self.data:
            return "The leaderboard is empty."
        start = self.page * self.per_page
        end = start + self.per_page
        current_chunk = self.data[start:end]

        description = f"The top donors of this server are:\n\n"
        for i, (user_id, amount) in enumerate(current_chunk, start=start + 1):
            description += f"``{i}.`` <@{user_id}> - {amount}\n"
        
        return description

    @discord.ui.button(emoji="<:leftwhitearrow:1463240103328747746>", style=discord.ButtonStyle.gray, custom_id="previous_page")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        embed=discord.Embed(
            title=f"Top **{self.category}** Donors", 
            description=self.create_description(), 
            color=0xc1e1ff
        )
        embed.set_footer(text=f"Page {self.page + 1} of {self.max_pages}")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="<:rightwhitearrow:1463240157359771658>", style=discord.ButtonStyle.gray, custom_id="next_page")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        embed=discord.Embed(
            title=f"Top **{self.category}** Donors", 
            description=self.create_description(), 
            color=0xc1e1ff
        )
        embed.set_footer(text=f"Page {self.page + 1} of {self.max_pages}")
        await interaction.response.edit_message(embed=embed, view=self)

async def create_leaderboard_embed(category: str, guild_id: int, rows: list[tuple[int, int]]) -> discord.Embed:
    embed = discord.Embed(title=f"Top **{category}** Donors", color=0xc1e1ff)
    embed.description = "The top donors of this server are:\n\n"
    if rows:
        for i, (user_id, amount) in enumerate(rows, start=1):
            embed.description += f"``{i}.`` <@{user_id}> - {amount}\n"
    else:
        embed.description += "The leaderboard is empty."
    
    amount_of_pages = (len(rows) // 10) + 1 if rows else 1
    embed.set_footer(text=f"Page 1 of {amount_of_pages}")
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)
    return embed

@donation.command(name="leaderboard", description="Show top donors per category")
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to view the leaderboard for")
async def leaderboard(interaction: discord.Interaction, category: str):
    await interaction.response.defer()
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    all_donations = guild_data["donations"]
    sorted_rows = []
    for uid, cats in all_donations.items():
        if category in cats and cats[category] > 0:
            sorted_rows.append((int(uid), cats[category]))
    
    sorted_rows.sort(key=lambda x: x[1], reverse=True)
    
    embed = await create_leaderboard_embed(category, interaction.guild.id, sorted_rows[:10])
    view = LeaderboardView(category, sorted_rows)
    msg = await interaction.followup.send(embed=embed, view=view)
    
    # Save active leaderboard message
    guild_data["active_leaderboards"][category] = {
        "channel_id": interaction.channel.id,
        "message_id": msg.id
    }
    bot.data_manager.save()

@don_category.command(name="create", description="Create a new donation category")
@is_manager_check()
@app_commands.describe(name="The name of the category")
async def c_create(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    if category_exists(name, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category already exists.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    guild_data["categories"].append(name)
    bot.data_manager.save()
    
    embed = discord.Embed(title="Category Created", description=f"Created category **{name}**", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_category.command(name="delete", description="Delete a donation category")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The name of the category")
async def c_delete(interaction: discord.Interaction, category: str):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    if category in guild_data["categories"]:
        guild_data["categories"].remove(category)
        
    # Also clean up donations related to this category if you want (optional)
    # for uid in guild_data["donations"]:
    #     if category in guild_data["donations"][uid]:
    #         del guild_data["donations"][uid][category]
    
    bot.data_manager.save()
    embed = discord.Embed(title="Category Deleted", description=f"Deleted category **{category}**.", color=discord.Color.red())
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_category.command(name="list", description="List all donation categories")
@is_manager_check()
async def c_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    categories = guild_data.get("categories", [])
    
    if not categories:
        embed = discord.Embed(title="No Categories", description="No donation categories found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    embed = discord.Embed(title="Donation Categories", description="", color=0x95d5ff)
    for cat in categories:
        embed.description += f"• {cat}\n"
    await interaction.followup.send(embed=embed)

@don_category.command(name="rename", description="Rename a donation category")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
async def c_rename(interaction: discord.Interaction, category: str, new_name: str):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    # Update category list
    if category in guild_data["categories"]:
        idx = guild_data["categories"].index(category)
        guild_data["categories"][idx] = new_name
    
    # Update donations
    for uid in guild_data["donations"]:
        if category in guild_data["donations"][uid]:
            guild_data["donations"][uid][new_name] = guild_data["donations"][uid].pop(category)
            
    # Update autoroles
    for ar in guild_data["autoroles"]:
        if ar["category"] == category:
            ar["category"] = new_name
            
    # Update active leaderboards
    if category in guild_data["active_leaderboards"]:
        guild_data["active_leaderboards"][new_name] = guild_data["active_leaderboards"].pop(category)

    bot.data_manager.save()
    embed = discord.Embed(title="Category Renamed", description=f"Renamed category **{category}** to **{new_name}**", color=0x95d5ff)
    await interaction.followup.send(embed=embed)

@don_category.command(name="reset", description="Reset donations in a category (all members or specific member)")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to reset donations for", member="Optional: Specified member to reset")
async def c_reset(interaction: discord.Interaction, category: str, member: Optional[discord.Member] = None):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    
    if member:
        uid = str(member.id)
        if uid in guild_data["donations"] and category in guild_data["donations"][uid]:
            guild_data["donations"][uid][category] = 0
            embed = discord.Embed(title="Donation Reset", description=f"Reset donations for {member.mention} in **{category}**.", color=discord.Color.orange())
        else:
            embed = discord.Embed(title="No Data", description=f"{member.mention} has no donation records in **{category}**.", color=discord.Color.orange())
    else:
        # Reset all members for this category
        count = 0
        for uid in guild_data["donations"]:
            if category in guild_data["donations"][uid]:
                guild_data["donations"][uid][category] = 0
                count += 1
        embed = discord.Embed(title="Category Reset", description=f"Reset donations for all **{count}** members in **{category}**.", color=discord.Color.orange())
    
    bot.data_manager.save()
    await bot.update_leaderboard_messages(interaction.guild.id, category)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="add_manager", description="Add a role as a manager")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(role="The role to add as a manager")
async def sm_add(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    if role.id in guild_data["managers"]:
        embed = discord.Embed(title="Error", description=f"{role.mention} is already a manager.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)
        
    guild_data["managers"].append(role.id)
    bot.data_manager.save()
    embed = discord.Embed(title="Success", description=f"Added {role.mention} to manager roles", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="list_manager", description="List all manager roles")
@is_manager_check()
async def sm_list(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    roles = [f"<@&{rid}>" for rid in guild_data.get("managers", [])]
    desc = '\n'.join(roles) if roles else 'None'
    embed = discord.Embed(title="Managers", description=desc, color=discord.Color.blue())
    await interaction.followup.send(embed=embed)

@don_settings.command(name="remove_manager", description="Remove a role from managers")
@app_commands.default_permissions(administrator=True)
async def sm_remove(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    if role.id not in guild_data["managers"]:
        embed = discord.Embed(title="Error", description="Manager role not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)
        
    guild_data["managers"].remove(role.id)
    bot.data_manager.save()
    embed = discord.Embed(title="Success", description=f"Removed {role.mention} from managers.", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="logging", description="Set the logging channel")
@app_commands.default_permissions(administrator=True)
async def s_log(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    guild_data["settings"]["log_channel_id"] = channel.id
    bot.data_manager.save()
    
    embed = discord.Embed(title="Logging Channel Set", description=f"Logs set to {channel.mention}", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_autorole.command(name="set", description="Set a threshold for a role reward")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to set the autorole for", role="The role to give", threshold="The donation threshold")
async def s_autorole(interaction: discord.Interaction, category: str, role: discord.Role, threshold: int):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    # Check if exists and update, or add new
    found = False
    for ar in guild_data["autoroles"]:
        if ar["category"] == category and ar["threshold"] == threshold:
            ar["role_id"] = role.id
            found = True
            break
    
    if not found:
        guild_data["autoroles"].append({
            "category": category,
            "threshold": threshold,
            "role_id": role.id
        })
    
    bot.data_manager.save()
    embed = discord.Embed(title="Autorole Set", description=f"Members who reach {threshold} donation points in **{category}** will now receive {role.mention}.", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))
    await update_existing_members(interaction, role, category, threshold)

@don_autorole.command(name="remove", description="Remove an autorole for a category")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to remove the autorole from", role="The role to remove", threshold="The threshold to remove")
async def r_autorole(interaction: discord.Interaction, category: str, role: discord.Role, threshold: int):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
        
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    original_len = len(guild_data["autoroles"])
    guild_data["autoroles"] = [ar for ar in guild_data["autoroles"] if not (ar["category"] == category and ar["threshold"] == threshold and ar["role_id"] == role.id)]
    
    if len(guild_data["autoroles"]) == original_len:
        embed = discord.Embed(title="Error", description="This autorole has not been set for this category and threshold.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
        
    bot.data_manager.save()
    embed = discord.Embed(title="Autorole Removed", description=f"Removed {role.mention} from **{category}** at threshold {threshold}.", color=0x95d5ff)
    await interaction.followup.send(embed=embed, ephemeral=True)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_autorole.command(name="list", description="List autoroles for a category")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to view autoroles for")
async def l_autorole(interaction: discord.Interaction, category: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    autoroles = guild_data.get("autoroles", [])
    
    if not autoroles:
        embed = discord.Embed(title="No Autoroles", description="No autoroles found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    if category:
        if not category_exists(category, interaction.guild.id):
            embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed, ephemeral=True)

        filtered = [ar for ar in autoroles if ar["category"] == category]
        if not filtered:
            embed = discord.Embed(title="No Autoroles", description=f"No autoroles found for **{category}**.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # Group by threshold
        grouped = {}
        for ar in filtered:
            thresh = ar["threshold"]
            if thresh not in grouped: grouped[thresh] = []
            grouped[thresh].append(ar["role_id"])

        description = ""
        for thresh in sorted(grouped.keys()):
            roles_str = ", ".join(f"<@&{rid}>" for rid in grouped[thresh])
            description += f"{roles_str} at {thresh} donations\n"

        embed = discord.Embed(title=f"Autoroles for **{category}**", description=description, color=0x95d5ff)
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        # Group by category then threshold
        grouped = {}
        for ar in autoroles:
            cat = ar["category"]
            thresh = ar["threshold"]
            if cat not in grouped: grouped[cat] = {}
            if thresh not in grouped[cat]: grouped[cat][thresh] = []
            grouped[cat][thresh].append(ar["role_id"])

        description = ""
        for cat in sorted(grouped.keys()):
            description += f"**{cat}**:\n"
            for thresh in sorted(grouped[cat].keys()):
                roles_str = ", ".join(f"<@&{rid}>" for rid in grouped[cat][thresh])
                description += f"- {roles_str} at {thresh} donations\n"
            description += "\n"

        embed = discord.Embed(title="Autoroles", description=description.strip(), color=0x95d5ff)
        await interaction.followup.send(embed=embed, ephemeral=True)

async def update_existing_members(interaction: discord.Interaction, role: discord.Role, category: str, threshold: int):
    guild_data = bot.data_manager.get_guild(interaction.guild.id)
    eligible_uids = []
    for uid, cats in guild_data["donations"].items():
        if cats.get(category, 0) >= threshold:
            eligible_uids.append(int(uid))
            
    for uid in eligible_uids:
        member = interaction.guild.get_member(uid)
        if member and role not in member.roles:
            try:
                await member.add_roles(role)
                log_embed = discord.Embed(title="Autorole Granted", description=f"Granted {role.mention} to <@{uid}> for donations in **{category}**.", color=0x95d5ff)
                log_embed.description += f"\n\n➕ Roles Granted:\n{role.mention}"
                await bot.log_action(interaction.guild, create_log_embed(interaction, log_embed, True))
            except discord.Forbidden:
                pass

@bot.tree.command(name="help", description="Show all bot commands (Admin Only)")
@app_commands.default_permissions(administrator=True)
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Donation Bot Help", color=0x95d5ff)
    
    # Grouping commands for the help embed
    embed.add_field(name="Donation Commands", value=(
        "`/donation add <member> <category> <amount>` - Add donations to a member\n"
        "`/donation remove <member> <category> <amount>` - Remove donations from a member\n"
        "`/donation leaderboard <category>` - View the donation leaderboard\n"
        "`/donation_check <member>` - Check a specific member's donation totals"
    ), inline=False)
    
    embed.add_field(name="Category Management", value=(
        "`/donation category create <name>` - Create a new category\n"
        "`/donation category delete <category>` - Delete a category\n"
        "`/donation category rename <category> <new_name>` - Rename a category\n"
        "`/donation category list` - List all categories\n"
        "`/donation category reset <category> [member]` - Reset donations (all or specific user)"
    ), inline=False)
    
    embed.add_field(name="Autorole Management", value=(
        "`/donation autorole set <category> <role> <threshold>` - Set a reward role\n"
        "`/donation autorole remove <category> <role> <threshold>` - Remove a reward role\n"
        "`/donation autorole list [category]` - List all reward roles"
    ), inline=False)
    
    embed.add_field(name="Settings", value=(
        "`/donation settings add_manager <role>` - Add an admin-level role\n"
        "`/donation settings remove_manager <role>` - Remove an admin-level role\n"
        "`/donation settings list_manager` - List all admin roles\n"
        "`/donation settings logging <channel>` - Set the logs channel"
    ), inline=False)
    
    embed.set_footer(text="Only administrators and managers can run most of these commands.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild_data = bot.data_manager.get_guild(role.guild.id)
    # Remove from managers
    if role.id in guild_data["managers"]:
        guild_data["managers"].remove(role.id)
    # Remove from autoroles
    guild_data["autoroles"] = [ar for ar in guild_data["autoroles"] if ar["role_id"] != role.id]
    bot.data_manager.save()

@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild: return
    guild_data = bot.data_manager.get_guild(message.guild.id)
    # Check active leaderboards
    to_del = []
    for cat, info in guild_data["active_leaderboards"].items():
        if info["message_id"] == message.id:
            to_del.append(cat)
    for cat in to_del:
        del guild_data["active_leaderboards"][cat]
    if to_del:
        bot.data_manager.save()

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if not channel.guild: return
    guild_data = bot.data_manager.get_guild(channel.guild.id)
    # Check active leaderboards
    to_del = []
    for cat, info in guild_data["active_leaderboards"].items():
        if info["channel_id"] == channel.id:
            to_del.append(cat)
    for cat in to_del:
        del guild_data["active_leaderboards"][cat]
    
    # Check logging channel
    if guild_data["settings"].get("log_channel_id") == channel.id:
        guild_data["settings"]["log_channel_id"] = None
        
    if to_del or guild_data["settings"]["log_channel_id"] is None:
        bot.data_manager.save()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # If the response was deferred, we need to use followup
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    
    if isinstance(error, discord.app_commands.errors.CheckFailure):
        embed = discord.Embed(title="Error", description="You do not have permission to use this command.", color=discord.Color.red())
    elif isinstance(error, discord.errors.Forbidden):
        embed = discord.Embed(title="Error", description="I don't have permissions to do that.", color=discord.Color.red())
    else:
        embed = discord.Embed(title="Error", description=str(error), color=discord.Color.red())
    
    try:
        await send(embed=embed, ephemeral=True)
    except:
        pass

bot.run(TOKEN)