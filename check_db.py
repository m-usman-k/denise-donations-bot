import sqlite3

conn = sqlite3.connect("donation_system.db")
cursor = conn.cursor()

print("Categories:")
cursor.execute("SELECT guild_id, name FROM categories")
for row in cursor.fetchall():
    print(row)

print("\nGuild IDs in DB:")
cursor.execute("SELECT DISTINCT guild_id FROM categories")
for row in cursor.fetchall():
    print(row)

conn.close()
