import sqlite3
import requests
import re
import datetime
import calendar

# --- Configuration ---
# Define the router IPs and the URLs for the stats pages.
# Update these with the correct URLs for your OpenWrt devices.
ROUTERS = {
    "192.168.1.1": {
        "ap_stats": "http://192.168.1.1/cgi-bin/totalwifi.cgi",
        "wan_stats": "http://192.168.1.1/cgi-bin/wan.cgi",
        "dhcp_leases": "http://192.168.1.1/cgi-bin/dhcp.cgi"
    },
    "192.168.1.2": {
        "ap_stats": "http://192.168.1.2/cgi-bin/totalwifi.cgi",
        "wan_stats": None,
        "dhcp_leases": None  # Removed DHCP collection from the AP
    }
}

# Separate database files for different data types
STATS_DB_NAME = "network_stats.db"
DHCP_DB_NAME = "dhcp_leases.db"

# --- Database Functions ---
def connect_db(db_name):
    """Establishes a connection to a specified SQLite database."""
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Database connection error for {db_name}: {e}")
        return None

def setup_stats_db(conn):
    """Creates tables for cumulative and monthly stats if they don't exist."""
    cursor = conn.cursor()
    
    try:
        # Table to store the last known cumulative values (for incremental calculation)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cumulative_stats (
                id TEXT PRIMARY KEY,
                rx_bytes INTEGER,
                tx_bytes INTEGER
            )
        """)
        
        # Table to store the monthly totals
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_stats (
                id TEXT PRIMARY KEY,
                rx_bytes INTEGER,
                tx_bytes INTEGER,
                timestamp TEXT
            )
        """)
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error setting up stats database tables: {e}")

def setup_dhcp_db(conn):
    """Creates the table for DHCP leases if it doesn't exist."""
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dhcp_leases (
                mac_address TEXT PRIMARY KEY,
                lease_end_time INTEGER,
                ip_address TEXT,
                hostname TEXT,
                client_id TEXT,
                timestamp TEXT
            )
        """)
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error setting up DHCP database table: {e}")

def reset_monthly_stats(conn):
    """
    Resets the monthly stats if the current month is different from the
    last update month. This is more robust than checking for the first day.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT timestamp FROM monthly_stats ORDER BY timestamp DESC LIMIT 1")
        last_update_row = cursor.fetchone()

        if last_update_row:
            last_update_month = datetime.datetime.strptime(last_update_row['timestamp'], '%Y-%m-%d %H:%M:%S').month
            current_month = datetime.datetime.now().month
            
            if last_update_month != current_month:
                print("New month detected. Resetting monthly stats.")
                cursor.execute("UPDATE monthly_stats SET rx_bytes = 0, tx_bytes = 0")
                conn.commit()
    except sqlite3.Error as e:
        print(f"Error during monthly stats reset: {e}")

# --- Data Fetching and Parsing Functions ---
def fetch_data(url):
    """
    Fetches text data from a given URL with a timeout.
    Returns the content as a string or None if an error occurs.
    """
    if not url:
        return None
        
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from {url}: {e}")
        return None

def parse_wifi_stats(data):
    """
    Parses client RX/TX data from the totalwifi.cgi output.
    Returns a list of dictionaries.
    """
    if not data:
        return []
    
    clients = []
    lines = data.strip().split('\n')
    for line in lines:
        parts = line.split()
        if len(parts) == 3:
            mac_address, rx_bytes, tx_bytes = parts
            clients.append({
                "mac_address": mac_address.lower(),
                "rx_bytes": int(rx_bytes),
                "tx_bytes": int(tx_bytes)
            })
    return clients

def parse_wan_stats(data):
    """
    Parses WAN RX/TX data from the wan.cgi output.
    Returns a single dictionary.
    """
    if not data:
        return None
        
    match = re.search(r"wan:\s+(\d+)\s+(\d+)", data)
    if match:
        rx_bytes = int(match.group(1))
        tx_bytes = int(match.group(2))
        return {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes
        }
    return None

def parse_dhcp_leases(data):
    """
    Parses DHCP lease data from the dhcp.cgi output.
    Focuses on IPv4 leases for simplicity.
    Returns a list of dictionaries.
    """
    if not data:
        return []

    leases = []
    lines = data.strip().split('\n')
    ipv4_lease_pattern = re.compile(
        r'(\d+)\s+([0-9a-fA-F:]{17})\s+([\d\.]+)\s+(.*?)\s+([\d0-9a-fA-F:]+)'
    )
    for line in lines:
        match = ipv4_lease_pattern.match(line)
        if match:
            lease_end_time, mac_address, ip_address, hostname, client_id = match.groups()
            
            hostname = hostname.strip()
            hostname = 'Unknown' if hostname == '*' else hostname.split()[0]
            
            leases.append({
                "lease_end_time": int(lease_end_time),
                "mac_address": mac_address.lower(),
                "ip_address": ip_address,
                "hostname": hostname,
                "client_id": client_id
            })
    return leases

# --- Database Update Functions ---
def update_traffic_stats(conn, entity_id, new_rx, new_tx):
    """
    Calculates incremental traffic and updates the monthly totals.
    This function handles router resets.
    """
    cursor = conn.cursor()
    
    try:
        # Get the last known cumulative stats
        cursor.execute("SELECT rx_bytes, tx_bytes FROM cumulative_stats WHERE id = ?", (entity_id,))
        last_stats = cursor.fetchone()
        
        # Initialize monthly stats if not present
        cursor.execute("SELECT * FROM monthly_stats WHERE id = ?", (entity_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT OR REPLACE INTO monthly_stats (id, rx_bytes, tx_bytes, timestamp) VALUES (?, ?, ?, ?)",
                           (entity_id, 0, 0, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()

        if last_stats:
            last_rx, last_tx = last_stats['rx_bytes'], last_stats['tx_bytes']
            
            # Calculate incremental traffic, handling resets
            incremental_rx = new_rx - last_rx if new_rx >= last_rx else new_rx
            incremental_tx = new_tx - last_tx if new_tx >= last_tx else new_tx
            
            # Update monthly total
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("""
                UPDATE monthly_stats
                SET rx_bytes = rx_bytes + ?,
                    tx_bytes = tx_bytes + ?,
                    timestamp = ?
                WHERE id = ?
            """, (incremental_rx, incremental_tx, timestamp, entity_id))
        
        # Update the last known cumulative stats with the new values
        cursor.execute("""
            INSERT OR REPLACE INTO cumulative_stats (id, rx_bytes, tx_bytes)
            VALUES (?, ?, ?)
        """, (entity_id, new_rx, new_tx))
        
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error updating traffic stats for {entity_id}: {e}")

def upsert_dhcp_leases(conn, leases_data):
    """Inserts or updates DHCP leases in the dedicated DHCP database."""
    if not leases_data:
        return
    
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor = conn.cursor()

    try:
        for lease in leases_data:
            cursor.execute("""
                INSERT OR REPLACE INTO dhcp_leases (mac_address, lease_end_time, ip_address, hostname, client_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                lease['mac_address'],
                lease['lease_end_time'],
                lease['ip_address'],
                lease['hostname'],
                lease['client_id'],
                timestamp
            ))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Error updating DHCP leases: {e}")


# --- Main Execution ---
def main():
    """Main function to orchestrate the data collection and storage."""
    try:
        conn_stats = connect_db(STATS_DB_NAME)
        conn_dhcp = connect_db(DHCP_DB_NAME)

        if not conn_stats or not conn_dhcp:
            return

        setup_stats_db(conn_stats)
        setup_dhcp_db(conn_dhcp)
        
        reset_monthly_stats(conn_stats)

        for router_ip, urls in ROUTERS.items():
            print(f"Processing Router: {router_ip}")
            
            ap_data = fetch_data(urls.get("ap_stats"))
            clients = parse_wifi_stats(ap_data)
            if clients:
                for client in clients:
                    update_traffic_stats(conn_stats, client['mac_address'], client['rx_bytes'], client['tx_bytes'])
                
            wan_data = fetch_data(urls.get("wan_stats"))
            wan = parse_wan_stats(wan_data)
            if wan:
                update_traffic_stats(conn_stats, "main_wan", wan['rx_bytes'], wan['tx_bytes'])

            dhcp_data = fetch_data(urls.get("dhcp_leases"))
            leases = parse_dhcp_leases(dhcp_data)
            if leases:
                upsert_dhcp_leases(conn_dhcp, leases)

        conn_stats.close()
        conn_dhcp.close()
        print("All stats processed and stored.")

    except Exception as e:
        print(f"An unhandled error occurred: {e}")

if __name__ == "__main__":
    main()
