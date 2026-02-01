import discord
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# --- Configuration ---
import os
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
VERCEL_API_URL = "https://leet-seek.vercel.app/api/handler" # Your Vercel URL
# Path to your firebase credentials file (download from Firebase Console)
FIREBASE_KEY_PATH = "serviceAccountKey.json" 

# --- Firebase Init ---
import base64 # Add this import at the top if missing

# Check if running in cloud (Env Var exists) or local (File exists)
if os.environ.get("FIREBASE_SERVICE_ACCOUNT_BASE64"):
    # Cloud Mode: Decode the Base64 string
    print("☁️ Detected Cloud Environment. Loading Firebase from Env Var.")
    base64_creds = os.environ.get('FIREBASE_SERVICE_ACCOUNT_BASE64')
    decoded_creds = base64.b64decode(base64_creds).decode('utf-8')
    service_account_info = json.loads(decoded_creds)
    cred = credentials.Certificate(service_account_info)
else:
    # Local Mode: Load from file
    print("💻 Detected Local Environment. Loading Firebase from file.")
    cred = credentials.Certificate(FIREBASE_KEY_PATH)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Bot Setup ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Helper Functions ---
def trigger_vercel_update(username):
    """Hits the Vercel API to force an update for a specific user."""
    try:
        # We assume your Vercel API accepts ?username=xyz
        response = requests.get(f"{VERCEL_API_URL}?username={username}", timeout=10)
        return response.status_code == 200
    except:
        return False

# --- Events ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)
    
    # Start the background task for frequent updates
    if not update_tracked_users.is_running():
        update_tracked_users.start()

# --- Background Task: The "Friends" Updater ---
# This runs every 30 minutes to update the "VIP" list
# --- Configuration Update ---
NOTIFICATION_CHANNEL_ID = 1467571263068311674  # <--- PASTE YOUR COPIED ID HERE (No quotes!)

@tasks.loop(minutes=30)
async def update_tracked_users():
    print("🔄 Starting priority update loop...")
    
    try:
        channel = await bot.fetch_channel(NOTIFICATION_CHANNEL_ID)
    except Exception as e:
        print(f"❌ Error: Could not find notification channel (ID: {NOTIFICATION_CHANNEL_ID}).")
        print(f"   Details: {e}")
        print("   Make sure the ID is correct and the Bot has 'View Channel' permissions there.")
        return

    # 1. Get list of tracked users
    tracked_ref = db.collection("trackedUsers").stream()
    tracked_users = [doc.id for doc in tracked_ref]
    
    if not tracked_users:
        return

    count = 0
    for username in tracked_users:
        # A. FETCH OLD DATA (Before Update)
        old_doc = db.collection("leetcodeUsers").document(username).get()
        old_total = 0
        if old_doc.exists:
            old_total = old_doc.to_dict().get("problems_solved", {}).get("All", 0)

        # B. TRIGGER UPDATE & GET NEW DATA
        # We assume the Vercel API returns the fresh data in the response body
        try:
            response = requests.get(f"{VERCEL_API_URL}?username={username}", timeout=30)
            
            if response.status_code == 200:
                new_data = response.json().get("data", {})
                new_total = new_data.get("problems_solved", {}).get("All", 0)
                
                # C. COMPARE & NOTIFY
                if new_total > old_total:
                    diff = new_total - old_total
                    
                    # Create a celebratory Embed
                    embed = discord.Embed(
                        title="🚀 New Problem Solved!",
                        description=f"**{username}** has solved **{diff}** new problem(s)!",
                        color=0x00FF00
                    )
                    embed.add_field(name="New Total", value=f"{new_total} Solved", inline=True)
                    
                    # Add last submission details if available
                    last_sub = new_data.get("last_submission")
                    if last_sub:
                        embed.add_field(name="Latest", value=f"[{last_sub['title']}]({last_sub['url']})", inline=False)
                        embed.set_footer(text=f"Language: {last_sub['lang']}")

                    await channel.send(embed=embed)
                    print(f"🔔 Notification sent for {username}")
                
                count += 1
            else:
                print(f"❌ Failed to update {username}")
                
        except Exception as e:
            print(f"❌ Error updating {username}: {e}")
    
    print(f"✅ Priority loop finished. Updated {count}/{len(tracked_users)} users.")

# --- Commands ---

# 1. /stats: Get user data (Reads from DB for speed)
@bot.tree.command(name="stats", description="View LeetCode stats for a user")
async def stats(interaction: discord.Interaction, username: str):
    await interaction.response.defer() # Avoid timeout while fetching
    
    # Check DB first
    doc_ref = db.collection("leetcodeUsers").document(username)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        
        # Calculate Total Solved
        solved = data.get("problems_solved", {})
        total = solved.get("All", 0)
        easy = solved.get("Easy", 0)
        medium = solved.get("Medium", 0)
        hard = solved.get("Hard", 0)
        
        # Create Embed
        embed = discord.Embed(title=f"LeetCode Stats: {data.get('name')}", url=f"https://leetcode.com/{username}", color=0xFFA116)
        embed.add_field(name="Total Solved", value=f"**{total}**", inline=False)
        embed.add_field(name="Easy", value=str(easy), inline=True)
        embed.add_field(name="Medium", value=str(medium), inline=True)
        embed.add_field(name="Hard", value=str(hard), inline=True)
        
        last_sub = data.get("last_submission")
        if last_sub:
            embed.add_field(name="Last Solved", value=f"[{last_sub['title']}]({last_sub['url']})", inline=False)
            embed.set_footer(text=f"Last updated: {last_sub['timestamp']}")
            
        await interaction.followup.send(embed=embed)
    else:
        # If not in DB, try to fetch it for the first time
        await interaction.followup.send(f"User **{username}** not found in database. Trying to fetch...")
        if trigger_vercel_update(username):
            await interaction.followup.send(f"Successfully added **{username}**! Run /stats again.")
        else:
            await interaction.followup.send("Could not find that user on LeetCode.")


# 2. /leaderboard: Shows top users from DB
@bot.tree.command(name="leaderboard", description="Show the global leaderboard")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    
    # Query Firestore: Get all users
    # Note: For production, you should maintain a separate sorted counter or limit this query
    users_ref = db.collection("leetcodeUsers").stream()
    
    # Sort in Python (easier for small datasets < 1000 users)
    # We sort by 'All' problems solved
    all_users = []
    for doc in users_ref:
        data = doc.to_dict()
        solved_count = data.get("problems_solved", {}).get("All", 0)
        all_users.append((data.get("username"), solved_count))
    
    # Sort descending
    all_users.sort(key=lambda x: x[1], reverse=True)
    
    # Take top 10
    top_10 = all_users[:10]
    
    embed = discord.Embed(title="🏆 LeetCode Leaderboard", color=0x00FF00)
    description = ""
    for i, (user, count) in enumerate(top_10, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"`{i}.`"
        description += f"{medal} **{user}** - {count} solved\n"
        
    embed.description = description
    await interaction.followup.send(embed=embed)


# 3. /track: Add a friend to the priority update list
@bot.tree.command(name="track", description="Add a user to the priority update list (updates every 30m)")
async def track(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    
    # 1. Verify user exists in main DB first
    user_doc = db.collection("leetcodeUsers").document(username).get()
    
    if not user_doc.exists:
        # Try to fetch them first
        if not trigger_vercel_update(username):
            await interaction.followup.send(f"❌ User **{username}** invalid or not found.")
            return

    # 2. Add to trackedUsers collection
    # We use .set({}) to create a blank document just to have the ID there
    db.collection("trackedUsers").document(username).set({"added_at": datetime.now()})
    
    await interaction.followup.send(f"✅ **{username}** is now being tracked! Stats will update every 30 minutes.")

bot.run(DISCORD_TOKEN)