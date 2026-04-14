import sqlite3
import json
import os

def migrate():
    conn = sqlite3.connect("donation_system.db")
    cursor = conn.cursor()

    data = {"guilds": {}}

    def get_guild(guild_id):
        gid = str(guild_id)
        if gid not in data["guilds"]:
            data["guilds"][gid] = {
                "categories": [],
                "donations": {},
                "settings": {"log_channel_id": None},
                "managers": [],
                "autoroles": [],
                "active_leaderboards": {}
            }
        return data["guilds"][gid]

    # Categories
    cursor.execute("SELECT guild_id, name FROM categories")
    for gid, name in cursor.fetchall():
        print(f"Migrating category {name} for guild {gid}")
        get_guild(gid)["categories"].append(name)

    # Donations
    cursor.execute("SELECT guild_id, user_id, category_name, amount FROM donations")
    for gid, uid, cat, amount in cursor.fetchall():
        g = get_guild(gid)
        uid = str(uid)
        if uid not in g["donations"]: g["donations"][uid] = {}
        g["donations"][uid][cat] = amount

    # Settings
    cursor.execute("SELECT guild_id, log_channel_id FROM settings")
    for gid, lid in cursor.fetchall():
        get_guild(gid)["settings"]["log_channel_id"] = lid

    # Managers
    cursor.execute("SELECT guild_id, role_id FROM managers")
    for gid, rid in cursor.fetchall():
        get_guild(gid)["managers"].append(rid)

    # Autoroles
    cursor.execute("SELECT guild_id, category_name, threshold, role_id FROM autoroles")
    for gid, cat, threshold, rid in cursor.fetchall():
        get_guild(gid)["autoroles"].append({
            "category": cat, "threshold": threshold, "role_id": rid
        })

    # Leaderboards
    cursor.execute("SELECT guild_id, category_name, channel_id, message_id FROM active_leaderboards")
    for gid, cat, cid, mid in cursor.fetchall():
        get_guild(gid)["active_leaderboards"][cat] = {"channel_id": cid, "message_id": mid}

    conn.close()

    # Load existing JSON if it exists to merge (so we don't lose the new guild if it's there)
    if os.path.exists("donations_data.json"):
        with open("donations_data.json", "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
                for gid, gdata in existing.get("guilds", {}).items():
                    if gid not in data["guilds"]:
                        data["guilds"][gid] = gdata
                    else:
                        # Existing guild in both, prefer migrated data but maybe keep some bits
                        pass 
            except: pass

    with open("donations_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print("Migration finished.")

if __name__ == "__main__":
    migrate()
