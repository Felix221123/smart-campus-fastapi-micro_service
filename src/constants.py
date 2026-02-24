import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Define global constants here
config = {
    "DATABASE_URL": os.getenv("DATABASE_URL"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
    "DB_SCHEMA": os.getenv("DB_SCHEMA", "public"),
    "JWT_SECRET": os.getenv("JWT_SECRET", "s3cr3t_k3y_with_$pecialChars_!@#123"),
    "JWT_ALGORITHM": os.getenv("JWT_ALGORITHM", "HS256"),
    "JWT_EXPIRATION_TIME": os.getenv("JWT_EXPIRATION_TIME", "1h"),
}
