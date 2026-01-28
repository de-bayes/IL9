#!/usr/bin/env python3
"""
Manual data repair script for Railway deployment.
Run this if the application is still experiencing JSON errors.
"""

import json
import os
from datetime import datetime

# Configuration
HISTORICAL_DATA_PATH = os.path.join(os.path.dirname(__file__), 'data', 'historical_snapshots.json')

def repair_corrupted_json():
    """Attempt to repair a JSON file with extra data"""
    try:
        if not os.path.exists(HISTORICAL_DATA_PATH):
            print("No historical data file found.")
            return False
            
        print(f"[{datetime.now().isoformat()}] Attempting to repair corrupted JSON file...")
        
        # Read the corrupted file
        with open(HISTORICAL_DATA_PATH, 'r') as f:
            content = f.read()
        
        print(f"[{datetime.now().isoformat()}] File size: {len(content)} characters")
        
        # Try normal parsing first
        try:
            data = json.loads(content)
            if isinstance(data, list):
                print(f"[{datetime.now().isoformat()}] File is actually valid! Contains {len(data)} snapshots.")
                return True
        except json.JSONDecodeError as e:
            print(f"[{datetime.now().isoformat()}] Confirmed corruption: {e}")
            
            # Handle "Extra data" error specifically
            if "Extra data" in str(e):
                print(f"[{datetime.now().isoformat()}] This is an 'Extra data' error - attempting recovery...")
                
                # Find the last complete JSON array
                last_bracket = content.rfind(']')
                if last_bracket > 0:
                    valid_content = content[:last_bracket + 1]
                    try:
                        recovered_data = json.loads(valid_content)
                        if isinstance(recovered_data, list):
                            print(f"[{datetime.now().isoformat()}] Successfully recovered {len(recovered_data)} snapshots!")
                            
                            # Create backup of corrupted file
                            backup_path = HISTORICAL_DATA_PATH + '.corrupted_' + datetime.now().strftime('%Y%m%d_%H%M%S')
                            with open(backup_path, 'w') as f:
                                f.write(content)
                            print(f"[{datetime.now().isoformat()}] Created backup: {backup_path}")
                            
                            # Save repaired version
                            temp_path = HISTORICAL_DATA_PATH + '.tmp'
                            with open(temp_path, 'w') as f:
                                json.dump(recovered_data, f, indent=2)
                            os.replace(temp_path, HISTORICAL_DATA_PATH)
                            
                            print(f"[{datetime.now().isoformat()}] Repair completed successfully!")
                            return True
                        else:
                            print(f"[{datetime.now().isoformat()}] Recovered data is not a list, cannot repair automatically.")
                            return False
                    except json.JSONDecodeError as recovery_error:
                        print(f"[{datetime.now().isoformat()}] Recovery failed: {recovery_error}")
                        return False
                else:
                    print(f"[{datetime.now().isoformat()}] No closing bracket found, file may be completely corrupted.")
                    return False
            else:
                print(f"[{datetime.now().isoformat()}] This is a different type of JSON error that requires manual intervention.")
                return False
                
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Unexpected error during repair: {e}")
        return False

if __name__ == '__main__':
    print("IL9 Primary Model Data Repair Tool")
    print("=" * 40)
    
    success = repair_corrupted_json()
    
    if success:
        print("\n✅ Repair completed successfully!")
        print("The application should now work correctly.")
    else:
        print("\n❌ Repair failed or not needed.")
        print("You may need to manually restore from backup or start fresh.")