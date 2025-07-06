#!/usr/bin/env python3
"""
MTGAbyss Worker Dependencies Installer
======================================

Installs the required packages for the unified worker.
"""

import subprocess
import sys

def install_package(package):
    """Install a package using pip"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"✅ Successfully installed {package}")
        return True
    except subprocess.CalledProcessError:
        print(f"❌ Failed to install {package}")
        return False

def main():
    print("MTGAbyss Worker Dependencies Installer")
    print("=" * 40)
    
    packages = [
        "requests",
        "google-generativeai",  # For Gemini
        "ollama",              # For Ollama
    ]
    
    print("Installing required packages...")
    
    success_count = 0
    for package in packages:
        if install_package(package):
            success_count += 1
    
    print(f"\nInstallation complete: {success_count}/{len(packages)} packages installed successfully")
    
    if success_count == len(packages):
        print("\n✅ All dependencies installed successfully!")
        print("\nYou can now use:")
        print("  python unified_worker.py --provider gemini")
        print("  python unified_worker.py --provider ollama --model llama3.1:8b")
        print("  python worker_parallel.py --provider gemini --workers 3")
    else:
        print("\n⚠️  Some packages failed to install. Check error messages above.")

if __name__ == "__main__":
    main()
