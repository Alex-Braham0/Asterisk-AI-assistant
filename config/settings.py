import json
import os
import sys
from pydantic import BaseModel, Field

class AppSettings(BaseModel):
    sip_ip: str = Field(..., description="IP address of the remote FreePBX/Asterisk Server.")
    sip_port: int = Field(5060, description="SIP port utilized for connection signaling.")
    my_ip: str = Field(..., description="Local IP address of the machine executing this assistant.")
    username: str = Field(..., description="The SIP extension numeric username credential.")
    password: str = Field(..., description="The SIP secret password credential.")
    gemini_api_key: str = Field(..., description="Google Gemini Live API Authentication Key.")
    openweathermap_api_key: str = Field("", description="OpenWeatherMap API Key utilized by the external weather lookup tool.")
    freepbx_db_ip: str = Field(..., description="Network IP address pointing to the FreePBX MariaDB/MySQL instance.")
    freepbx_db_user: str = Field(..., description="Database username authorized to read FreePBX users table.")
    freepbx_db_pass: str = Field(..., description="Database password authorized to read FreePBX users table.")
    postgres_url: str = Field("postgres://postgres:postgres@localhost:5432/asterisk_ai", description="PostgreSQL connection string string for core app transactional persistence.")
    system_prompt: str = Field("You are a helpful phone assistant. Give concise answers.", description="Base prompt engineering behavioral guide layer used by the ContextBuilder.")

    @classmethod
    def load_or_create(cls, config_path: str = "config.json") -> "AppSettings":
        """
        Loads the application configuration file. If the configuration file is missing,
        it builds a clean default template model, writes it to disk, and halts system execution.
        """
        if not os.path.exists(config_path):
            print(f"[Config Error] Configuration file '{config_path}' was not found.")
            print("Generating a clean schema deployment configuration template file...")
            
            default_template = {
                "sip_ip": "192.168.1.100",
                "sip_port": 5060,
                "my_ip": "192.168.1.98",
                "username": "100",
                "password": "secretpassword",
                "gemini_api_key": "YOUR_GEMINI_API_KEY",
                "openweathermap_api_key": "YOUR_OPENWEATHERMAP_API_KEY",
                "freepbx_db_ip": "192.168.1.100",
                "freepbx_db_user": "pbxsync",
                "freepbx_db_pass": "your_mysql_password",
                "postgres_url": "postgres://postgres:postgres@localhost:5432/asterisk_ai",
                "system_prompt": "You are Winston, a helpful phone assistant. Give concise answers."
            }
            
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(default_template, f, indent=4)
                print(f"[Config Template Saved] File initialized at '{config_path}'. Populate with real parameters and reboot application.")
            except Exception as e:
                print(f"[Config Initialization Critical Failure] Could not create deployment file template: {e}")
                
            sys.exit(1)

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            validated_settings = cls(**raw_data)
            return validated_settings
        except Exception as e:
            print(f"[Config Critical Error] Existing configuration file at '{config_path}' failed strict Pydantic validation rules: {e}")
            sys.exit(1)