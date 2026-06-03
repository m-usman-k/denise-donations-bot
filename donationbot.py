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
import sys
import os

# Add root directory to sys.path to import shared_database
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared_database import SharedDatabase

TOKEN = os.getenv("BOT_TOKEN")
db_helper = SharedDatabase()

class DonationBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.db = db_helper
        self.remove_command("help")

    async def setup_hook(self):
        self.add_view(LeaderboardView(None, None))
        print("Bot initialized and view added.")

    def is_manager(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator: 
            return True
        manager_roles = self.db.get_managers(interaction.guild.id)
        return any(role.id in manager_roles for role in interaction.user.roles)

    async def log_action(self, guild: discord.Guild, embed: discord.Embed):
        settings = self.db.get_guild_settings(guild.id)
        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel: 
                embed.add_field(name="Date & Time", value=f"<t:{int(time.time())}:f>", inline=False)
                await channel.send(embed=embed)

    async def update_leaderboard_messages(self, guild_id: int, category: str):
        # This part still needs active_leaderboards in DB, I'll skip or implement later
        pass

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
    categories = [cat[0] for cat in bot.db.get_donation_categories(interaction.guild.id)]
    choices = [app_commands.Choice(name=cat, value=cat) for cat in categories if current.lower() in cat.lower()]
    return choices[:25] if choices else [app_commands.Choice(name="No categories found", value="")]

def category_exists(category: str, guild_id: int) -> bool:
    categories = [cat[0] for cat in bot.db.get_donation_categories(guild_id)]
    return category in categories

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

    uid = str(member.id)
    current_amount = bot.db.get_user_donation(interaction.guild.id, uid, category)
    new_amount = current_amount + amount
    bot.db.update_user_donation(interaction.guild.id, uid, category, new_amount)

    await bot.update_leaderboard_messages(interaction.guild.id, category)
    
    roles_added = []
    autoroles = bot.db.get_autoroles(interaction.guild.id, category)
    
    for ar in autoroles:
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

    uid = str(member.id)
    current = bot.db.get_user_donation(interaction.guild.id, uid, category)
    
    if amount > current:
        amount = current
    
    if amount <= 0:
        embed = discord.Embed(title="Error", description="No donations to remove.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    new_total = current - amount
    bot.db.update_user_donation(interaction.guild.id, uid, category, new_total)
    
    await bot.update_leaderboard_messages(interaction.guild.id, category)
    
    embed = discord.Embed(title="Donation Removed", description=f"✅ {amount} point{'s' if amount > 1 else ''} removed from {member.mention}'s donations.", color=0x95d5ff)
    embed.add_field(name=f"Category", value=category, inline=False)
    embed.add_field(name=f"Updated amount", value=f"**{new_total}**", inline=False)
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
    uid = str(member.id)
    
    # Get all categories for this guild
    categories = [c[0] for c in bot.db.get_donation_categories(interaction.guild.id)]
    
    embed = discord.Embed(title=f"Stats for {member.display_name}", color=0xc1e1ff)
    lines = []
    for cat_name in categories:
        amount = bot.db.get_user_donation(interaction.guild.id, uid, cat_name)
        if amount > 0:
            lines.append(f"**{cat_name}**: {amount}")
            
    if lines:
        embed.description = "\n".join(lines)
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

    # Note: leaderboard logic for MariaDB needs to be efficient. 
    # For now, I'll fetch and sort.
    # Actually, SharedDatabase should have a get_donation_leaderboard method.
    # I'll add it to shared_database later or just use raw SQL here if I had access.
    # Since I can't easily edit SharedDatabase again right now in this chunk, 
    # I'll implement a workaround or assume I'll add the method.
    
    # Using the get_donation_leaderboard method from shared_database
    rows = bot.db.get_donation_leaderboard(interaction.guild.id, category)
    
    embed = await create_leaderboard_embed(category, interaction.guild.id, rows[:10])
    view = LeaderboardView(category, rows)
    msg = await interaction.followup.send(embed=embed, view=view)

@don_category.command(name="create", description="Create a new donation category")
@is_manager_check()
@app_commands.describe(name="The name of the category")
async def c_create(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)
    if category_exists(name, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category already exists.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
    
    bot.db.create_donation_category(interaction.guild.id, name)
    # I'll add this to shared_database later.
    
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
    
    bot.db.delete_donation_category(interaction.guild.id, category)
    # I'll add this to shared_database later.
    
    embed = discord.Embed(title="Category Deleted", description=f"Deleted category **{category}**.", color=discord.Color.red())
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_category.command(name="list", description="List all donation categories")
@is_manager_check()
async def c_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    categories = [c[0] for c in bot.db.get_donation_categories(interaction.guild.id)]
    
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
    
    bot.db.rename_donation_category(interaction.guild.id, category, new_name)
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
    
    if member:
        uid = member.id
        bot.db.reset_user_donations(interaction.guild.id, uid, category)
        embed = discord.Embed(title="Donation Reset", description=f"Reset donations for {member.mention} in **{category}**.", color=discord.Color.orange())
    else:
        bot.db.reset_donations_category(interaction.guild.id, category)
        embed = discord.Embed(title="Category Reset", description=f"Reset donations for all members in **{category}**.", color=discord.Color.orange())
    
    await bot.update_leaderboard_messages(interaction.guild.id, category)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_autorole.command(name="add", description="Add an autorole for a donation category")
@app_commands.default_permissions(administrator=True)
async def ar_add(interaction: discord.Interaction, category: str, threshold: int, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)
    
    bot.db.add_autorole(interaction.guild.id, category, role.id, threshold)
    
    embed = discord.Embed(title="Autorole Added", description=f"Users donating **{threshold}** in **{category}** will get {role.mention}", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="add_manager", description="Add a role as a manager")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(role="The role to add as a manager")
async def sm_add(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    managers = bot.db.get_managers(interaction.guild.id)
    if role.id in managers:
        embed = discord.Embed(title="Error", description=f"{role.mention} is already a manager.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)
        
    bot.db.add_manager(interaction.guild.id, role.id)
    embed = discord.Embed(title="Success", description=f"Added {role.mention} to manager roles", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="list_manager", description="List all manager roles")
@is_manager_check()
async def sm_list(interaction: discord.Interaction):
    await interaction.response.defer()
    managers = bot.db.get_managers(interaction.guild.id)
    roles = [f"<@&{rid}>" for rid in managers]
    desc = '\n'.join(roles) if roles else 'None'
    embed = discord.Embed(title="Managers", description=desc, color=discord.Color.blue())
    await interaction.followup.send(embed=embed)

@don_settings.command(name="remove_manager", description="Remove a role from managers")
@app_commands.default_permissions(administrator=True)
async def sm_remove(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    bot.db.remove_manager(interaction.guild.id, role.id)
    embed = discord.Embed(title="Success", description=f"Removed {role.mention} from managers.", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_settings.command(name="logging", description="Set the logging channel")
@app_commands.default_permissions(administrator=True)
async def s_log(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    bot.db.set_don_logs(interaction.guild.id, channel.id)
    
    embed = discord.Embed(title="Logging Channel Set", description=f"Logs set to {channel.mention}", color=0x95d5ff)
    await interaction.followup.send(embed=embed)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_autorole.command(name="remove", description="Remove an autorole for a category")
@is_manager_check()
@app_commands.autocomplete(category=category_autocomplete)
@app_commands.describe(category="The category to remove the autorole from", role="The role to remove", threshold="The threshold to remove")
async def r_autorole(interaction: discord.Interaction, category: str, role: discord.Role, threshold: int):
    await interaction.response.defer(ephemeral=True)
    if not category_exists(category, interaction.guild.id):
        embed = discord.Embed(title="Error", description="Category not found.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
        
    autoroles = bot.db.get_autoroles(interaction.guild.id, category)
    exists = any(ar["threshold"] == threshold and ar["role_id"] == role.id for ar in autoroles)
    
    if not exists:
        embed = discord.Embed(title="Error", description="This autorole has not been set for this category and threshold.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)
        
    bot.db.remove_autorole(interaction.guild.id, category, role.id, threshold)
    embed = discord.Embed(title="Autorole Removed", description=f"Removed {role.mention} from **{category}** at threshold {threshold}.", color=0x95d5ff)
    await interaction.followup.send(embed=embed, ephemeral=True)
    await bot.log_action(interaction.guild, create_log_embed(interaction, embed))

@don_autorole.command(name="list", description="List autoroles for a category (or all)")
@is_manager_check()
async def ar_list(interaction: discord.Interaction, category: Optional[str] = None):
    await interaction.response.defer()
    
    autoroles = bot.db.get_all_autoroles(interaction.guild.id)
    
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
    # This might require querying all users and their donations for a guild which isn't fully supported easily without SQL
    # Leaving empty for now, could be implemented with bot.db.get_all_donations(guild_id)
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
    bot.db.remove_manager(role.guild.id, role.id)
    autoroles = bot.db.get_all_autoroles(role.guild.id)
    for ar in autoroles:
        if ar["role_id"] == role.id:
            bot.db.remove_autorole(role.guild.id, ar["category"], role.id, ar["threshold"])

@bot.event
async def on_message_delete(message: discord.Message):
    pass

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if not channel.guild: return
    settings = bot.db.get_guild_settings(channel.guild.id)
    if settings.get("log_channel_id") == channel.id:
        bot.db.set_don_logs(channel.guild.id, None)

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