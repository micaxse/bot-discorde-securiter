# bot.py
import os
import logging
import unicodedata
from datetime import timedelta, datetime

from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

from keep_alive import keep_alive

# ==========================
# Config & logging
# ==========================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise SystemExit("❌ DISCORD_TOKEN introuvable dans .env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# ==========================
# Intents & bot
# ==========================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ==========================
# Anti-spam
# ==========================
SPAM_THRESHOLD = 5        # messages max
SPAM_WINDOW_MS = 10_000   # 10s
MUTE_DURATION_SEC = 5 * 60

spam_map: dict[int, list[int]] = {}  # user_id -> [timestamps ms]

def is_spam(user_id: int) -> bool:
    now = int(datetime.now().timestamp() * 1000)
    arr = [t for t in spam_map.get(user_id, []) if now - t < SPAM_WINDOW_MS]
    arr.append(now)
    spam_map[user_id] = arr
    return len(arr) > SPAM_THRESHOLD

# ==========================
# Détection d'insultes
# ==========================
FORBIDDEN_WORDS = [
    'connard', 'salope', 'pute', 'encule', 'fdp',
    'shit', 'fuck', 'bitch', 'asshole', 'damn', 'cunt', 'whore'
]

def normalize_text(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    s = s.replace('@', 'a').replace('$', 's').replace('€', 'e').replace('§', 's').replace('5', 's')
    out = []
    for ch in s:
        out.append(ch if (ch.isalnum() or ch.isspace()) else ' ')
    return ' '.join(''.join(out).split())

def contains_bad_words(message: str) -> bool:
    text = normalize_text(message)
    words = set(text.split())
    return any(w in words for w in FORBIDDEN_WORDS)

# ==========================
# Utilitaires modération
# ==========================
def bot_member(guild: discord.Guild) -> discord.Member | None:
    return guild.me  # type: ignore[return-value]

def can_moderate(guild: discord.Guild | None, target: discord.Member) -> bool:
    if guild is None:
        return False
    me = bot_member(guild)
    if me is None:
        return False
    if target.guild_permissions.administrator:
        return False
    if target.id == guild.owner_id:
        return False
    return me.top_role > target.top_role

async def safe_delete_message(message: discord.Message):
    try:
        me = bot_member(message.guild) if message.guild else None
        if me and me.guild_permissions.manage_messages:
            await message.delete()
    except Exception:
        pass

async def timeout_member(member: discord.Member, seconds: int, reason: str) -> bool:
    try:
        if not can_moderate(member.guild, member):
            return False
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        return True
    except Exception:
        logging.exception("Erreur timeout")
        return False

async def remove_timeout(member: discord.Member) -> bool:
    try:
        await member.timeout(None, reason="Timeout retiré")
        return True
    except Exception:
        return False

# ==========================
# Événements
# ==========================
@tree.command(name="ping", description="Vérifie si le bot est en ligne")
async def ping_cmd(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! Latence : {latency_ms} ms")

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logging.info(f"✅ Connecté en tant que {bot.user} • Slash commands synchronisées.")
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="Protéger le serveur 🛡️"),
            status=discord.Status.online
        )
    except Exception:
        logging.exception("Erreur lors du sync des commandes")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    member = message.guild.get_member(message.author.id)
    if member is None:
        try:
            member = await message.guild.fetch_member(message.author.id)
        except Exception:
            return

    if member.guild_permissions.moderate_members:
        return

    violation_reasons: list[str] = []

    if contains_bad_words(message.content):
        violation_reasons.append("Langage inapproprié")
        await safe_delete_message(message)

    if is_spam(member.id):
        violation_reasons.append("Spam")

    if violation_reasons:
        reason = " + ".join(violation_reasons)
        ok = await timeout_member(member, MUTE_DURATION_SEC, reason=reason)

        try:
            embed = discord.Embed(
                title="🚫 Modération Automatique",
                description=f"{member.mention} a été mis en timeout pour: **{reason}**",
                colour=discord.Colour.red()
            )
            await message.channel.send(embed=embed)
        except Exception:
            pass

        try:
            dm = discord.Embed(
                title="⚠️ Avertissement",
                description=f"Vous avez été temporairement restreint sur **{message.guild.name}** pour: **{reason}**",
                colour=discord.Colour.red()
            )
            dm.add_field(name="Durée", value="5 minutes", inline=True)
            dm.add_field(name="Serveur", value=message.guild.name, inline=True)
            await member.send(embed=dm)
        except Exception:
            pass

    await bot.process_commands(message)

# ==========================
# Slash Commands
# ==========================
@app_commands.default_permissions(kick_members=True)
@tree.command(name="kick", description="Expulser un membre du serveur")
@app_commands.describe(utilisateur="L'utilisateur à expulser", raison="Raison de l'expulsion")
async def kick_cmd(interaction: discord.Interaction, utilisateur: discord.Member, raison: str | None = None):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    if not can_moderate(interaction.guild, utilisateur):  # type: ignore[arg-type]
        return await interaction.response.send_message("❌ Je ne peux pas modérer cet utilisateur (hiérarchie/permissions).", ephemeral=True)
    try:
        await utilisateur.kick(reason=raison or "Aucune raison spécifiée")
        await interaction.response.send_message(f"✅ {utilisateur} a été expulsé. Raison: {raison or 'Aucune raison spécifiée'}")
    except Exception:
        await interaction.response.send_message("❌ Je ne peux pas expulser cet utilisateur.", ephemeral=True)

@app_commands.default_permissions(ban_members=True)
@tree.command(name="ban", description="Bannir un membre du serveur")
@app_commands.describe(utilisateur="L'utilisateur à bannir", raison="Raison du bannissement")
async def ban_cmd(interaction: discord.Interaction, utilisateur: discord.Member | discord.User, raison: str | None = None):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    guild = interaction.guild
    assert guild is not None
    if isinstance(utilisateur, discord.Member) and not can_moderate(guild, utilisateur):
        return await interaction.response.send_message("❌ Je ne peux pas modérer cet utilisateur (hiérarchie/permissions).", ephemeral=True)
    try:
        await guild.ban(utilisateur, reason=raison or "Aucune raison spécifiée")  # type: ignore[arg-type]
        await interaction.response.send_message(f"✅ {utilisateur} a été banni. Raison: {raison or 'Aucune raison spécifiée'}")
    except Exception:
        await interaction.response.send_message("❌ Je ne peux pas bannir cet utilisateur.", ephemeral=True)

@app_commands.default_permissions(moderate_members=True)
@tree.command(name="mute", description="Mettre un membre en timeout (mute natif)")
@app_commands.describe(utilisateur="L'utilisateur à muter", duree="Durée en minutes (défaut: 5)", raison="Raison du mute")
async def mute_cmd(interaction: discord.Interaction, utilisateur: discord.Member, duree: int | None = 5, raison: str | None = None):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    if not can_moderate(interaction.guild, utilisateur):  # type: ignore[arg-type]
        return await interaction.response.send_message("❌ Je ne peux pas modérer cet utilisateur (hiérarchie/permissions).", ephemeral=True)
    minutes = duree or 5
    ok = await timeout_member(utilisateur, minutes * 60, raison or "Aucune raison spécifiée")
    if ok:
        await interaction.response.send_message(f"✅ {utilisateur} a été mis en timeout pour {minutes} minutes. Raison: {raison or 'Aucune raison spécifiée'}")
    else:
        await interaction.response.send_message("❌ Erreur lors du mute.", ephemeral=True)

@app_commands.default_permissions(moderate_members=True)
@tree.command(name="unmute", description="Retirer le timeout d'un membre")
@app_commands.describe(utilisateur="L'utilisateur à démuter")
async def unmute_cmd(interaction: discord.Interaction, utilisateur: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    ok = await remove_timeout(utilisateur)
    if ok:
        await interaction.response.send_message(f"✅ {utilisateur} a été démuté.")
    else:
        await interaction.response.send_message("❌ Cet utilisateur n'est pas en timeout ou erreur.", ephemeral=True)

@app_commands.default_permissions(manage_messages=True)
@tree.command(name="clear", description="Supprimer des messages (1-100)")
@app_commands.describe(nombre="Nombre de messages à supprimer (1-100)")
async def clear_cmd(interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100]):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send("❌ Cette commande ne peut être utilisée que dans un salon texte.", ephemeral=True)
    try:
        deleted = await channel.purge(limit=nombre, reason=f"Clear demandé par {interaction.user}")
        await interaction.followup.send(f"✅ {len(deleted)} messages supprimés !", ephemeral=True)
    except Exception:
        await interaction.followup.send("❌ Erreur lors de la suppression.", ephemeral=True)

@app_commands.default_permissions(moderate_members=True)
@tree.command(name="warn", description="Avertir un membre (MP + log)")
@app_commands.describe(utilisateur="L'utilisateur à avertir", raison="Raison de l'avertissement")
async def warn_cmd(interaction: discord.Interaction, utilisateur: discord.User, raison: str):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
    embed = discord.Embed(
        title="⚠️ Avertissement",
        description=f"{utilisateur.mention} a reçu un avertissement",
        colour=discord.Colour.orange()
    )
    embed.add_field(name="Raison", value=raison)
    embed.set_footer(text=f"Par {interaction.user}")
    embed.timestamp = discord.utils.utcnow()
    await interaction.response.send_message(embed=embed)
    try:
        await utilisateur.send(content=f"⚠️ Vous avez reçu un avertissement sur **{interaction.guild.name}** pour : {raison}")  # type: ignore[union-attr]
    except Exception:
        pass

@tree.command(name="modhelp", description="Afficher l'aide pour les commandes de modération")
async def modhelp_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ Commandes de Modération",
        description="Voici toutes les commandes disponibles :",
        colour=discord.Colour.blue()
    )
    embed.add_field(name="/kick", value="Expulser un membre", inline=True)
    embed.add_field(name="/ban", value="Bannir un membre", inline=True)
    embed.add_field(name="/mute", value="Timeout d'un membre", inline=True)
    embed.add_field(name="/unmute", value="Retirer le timeout", inline=True)
    embed.add_field(name="/clear", value="Supprimer des messages", inline=True)
    embed.add_field(name="/warn", value="Avertir un membre", inline=True)
    embed.set_footer(text="Bot de Sécurité • Protection automatique active")
    embed.timestamp = discord.utils.utcnow()
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ==========================
# Gestion erreurs globales
# ==========================
@bot.event
async def on_error(event_method, *args, **kwargs):
    logging.exception(f"Erreur discord.py dans {event_method}")

# ==========================
# Lancement
# ==========================
if __name__ == "__main__":
    print("✅ Token OK (longueur):", len(DISCORD_TOKEN))
    print("✅ discord.py =", discord.__version__)
    keep_alive()          # lance Flask en thread
    bot.run(DISCORD_TOKEN)        # lance le bot (une seule fois)

