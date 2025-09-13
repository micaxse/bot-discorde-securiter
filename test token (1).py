import os
from dotenv import load_dotenv
load_dotenv()
tok = os.getenv("DISCORD_TOKEN")
print("Token lu:", ("OK, longueur="+str(len(tok)) if tok else "AUCUN TOKEN"))
