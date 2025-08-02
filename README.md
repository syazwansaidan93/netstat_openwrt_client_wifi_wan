# OpenWrt Stats Collector

A lightweight and efficient solution for collecting and analyzing network traffic statistics from OpenWrt routers. This project consists of a Python script to gather data, a PHP API to expose it, and a simple HTML dashboard to visualize it, giving you a powerful way to monitor your network.

The system is designed to provide accurate monthly traffic totals for your WAN and individual clients, even in the event of router reboots or client reconnections.

### Key Features

* **Accurate Monthly Reporting:** Provides true monthly totals for data usage, unaffected by resets or disconnections.

* **Minimal Database Growth:** Uses an efficient database design to keep storage requirements extremely low, making it suitable for low-power devices like a Raspberry Pi or Orange Pi.

* **Simple PHP API:** Exposes network data through a RESTful API, making it easy to build your own dashboard or integrate with other tools.

* **Visual Dashboard:** An included HTML file with a minimal dashboard to visualize your data without needing a complex frontend framework.

* **Hourly Data Collection:** The Python script is designed to be run hourly, balancing data granularity with system load.

* **DHCP Lease Tracking:** Correlates traffic data with DHCP leases to provide human-readable hostnames for connected devices.

### Requirements

* **OpenWrt Router:** Must be running OpenWrt. The Python script relies on OpenWrt's `cgi-bin` to fetch data.

* **Web Server:** A web server (e.g., Apache, Nginx) with PHP support.

* **PHP:** Must have the `sqlite3` extension enabled.

* **Python 3:** With the `requests` library.

### OpenWrt CGI Scripts

The Python script fetches all its data by calling simple shell scripts on your OpenWrt router via HTTP. You need to create these scripts on your router and place them in the `/www/cgi-bin/` directory.

#### `totalwifi.cgi`

This script collects `rx_bytes` and `tx_bytes` for each connected wireless client.

```
#!/bin/sh
echo "Content-type: text/plain"
echo ""

for iface in $(iw dev | awk '$1=="Interface"{print $2}'); do
    iw dev "$iface" station dump | awk '
        $1=="Station" {mac=$2}
        $1=="rx" && $2=="bytes:" {rx=$3}
        $1=="tx" && $2=="bytes:" {tx=$3; print mac, rx, tx}
    '
done

```

#### `dhcp.cgi`

This script retrieves a list of all active DHCP leases, which the Python script uses to map MAC addresses to hostnames.

```
#!/bin/sh
echo "Content-type: text/plain"
echo ""
cat /tmp/dhcp.leases

```

#### `wan.cgi`

This script reports the total `rx_bytes` and `tx_bytes` for the WAN interface of your router.

```
#!/bin/sh

echo "Content-type: text/plain"
echo ""
awk '$1=="wan:" {print "wan:", $2, $10}' /proc/net/dev

```

**After creating the files, make them executable:**

```
chmod +x /www/cgi-bin/totalwifi.cgi
chmod +x /www/cgi-bin/dhcp.cgi
chmod +x /www/cgi-bin/wan.cgi

```

---

### Setup & Installation

Follow these steps to get the project up and running.

#### 1. Set File Permissions

You need to ensure the `wan` user has permission to create and write to the database files within the `netstat` directory. Run this command to change the ownership of the directory.

```
sudo chown -R wan:wan /home/wan/netstat

```

#### 2. Configure the Python Script

Open the `router_stats.py` file and edit the `ROUTERS` dictionary to match the IP addresses and `cgi-bin` paths of your OpenWrt routers.

```
# router_stats.py
ROUTERS = {
    "192.168.1.1": {
        "ap_stats": "[http://192.168.1.1/cgi-bin/totalwifi.cgi](http://192.168.1.1/cgi-bin/totalwifi.cgi)",
        "wan_stats": "[http://192.168.1.1/cgi-bin/wan.cgi](http://192.168.1.1/cgi-bin/wan.cgi)",
        "dhcp_leases": "[http://192.168.1.1/cgi-bin/dhcp.cgi](http://192.168.1.1/cgi-bin/dhcp.cgi)"
    },
    # Add other routers here if needed
}

```

#### 3. Install Python Dependencies

The script requires the `requests` library. Install it using `pip3`.

```
sudo pip3 install requests

```

#### 4. Configure the PHP API

Place the `api.php` file in a directory accessible by your web server, for example, `/var/www/html/netstat/`.

Then, edit the database paths in `api.php` to point to the correct location where your Python script will store the SQLite files.

```
// api.php
$statsDbPath = '/home/wan/netstat/network_stats.db';
$dhcpDbPath = '/home/wan/netstat/dhcp_leases.db';

```

#### 5. Configure Nginx

To serve the PHP API and the HTML dashboard, you need to configure your web server. Here is a sample Nginx configuration.

```nginx
server {
    listen 80;
    server_name data.home;

    root /var/www/html/netstat;
    index index.html index.php;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
        fastcgi_index index.php;
    }

    location ~ \.db$ {
        deny all;
        return 403;
    }
}
```
After saving the Nginx configuration, you need to enable it and reload Nginx.

```
sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/
sudo systemctl restart nginx

```

#### 6. Scheduling the Script

You have two options for scheduling the script to run automatically.

**Option A: Using Cron (Recommended for simplicity)**

Add a cron job for the `wan` user to run the script every hour.

Open the crontab for the `wan` user:

```bash
crontab -e

```

Add the following line to the file to run the script every hour:

```bash
0 * * * * /usr/bin/python3 /home/wan/netstat/router_stats.py >> /var/log/router_stats.log 2>&1

```

This command runs the script at the beginning of every hour and pipes the output to a log file for easy debugging.

**Option B: Using systemd Timers (Recommended for reliability and logging)**

This is a more modern and robust method for scheduling tasks. You'll need to create two files: a service unit and a timer unit.

First, create the service file at `/etc/systemd/system/router_stats.service`:

```ini
[Unit]
Description=OpenWrt Network Stats Collector Service
After=network.target

[Service]
# User to run the script as
User=wan
# Set the working directory to where your script is located
WorkingDirectory=/home/wan/netstat
# The command to execute, using the python3 interpreter from the virtual environment
ExecStart=/home/wan/netstat/venv/bin/python3 /home/wan/netstat/router_stats.py
# Optional: Restart the service if it fails.
Restart=on-failure
```

Next, create the timer file at `/etc/systemd/system/router_stats.timer`:

```ini
[Unit]
Description=Run OpenWrt Network Stats Collector every hour

[Timer]
# Run every hour
OnCalendar=hourly
# Run the timer immediately on boot if it was scheduled to run in the past.
Persistent=true

[Install]
WantedBy=timers.target
```

Finally, enable and start the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable router_stats.timer
sudo systemctl start router_stats.timer
```

### Frontend Dashboard (`index.html`)

The included `index.html` file provides a simple dashboard to visualize the data collected by the Python script and exposed by the PHP API. The dashboard has a clean, dark-themed design and is fully responsive, making it easy to view on both desktop and mobile devices.

* **WAN Interface Statistics:** The top section of the dashboard displays the total Download and Upload traffic for your WAN interface, along with a timestamp of the last data update.

* **Connected Clients:** Below the WAN stats, a table shows a list of all connected client devices. Each row displays the device's Hostname/IP, its total downloaded data, and its total uploaded data, providing a quick overview of network usage per device.

* **Placement:** Copy `index.html` to your web server's root directory, typically `/var/www/html/netstat/`.

* **Technology:** This is a static HTML file that uses vanilla JavaScript to fetch data from the `/netstat/api.php?action=combined` endpoint. It's styled with **CSS** and uses **Chart.js** to render the traffic graphs.

* **Functionality:** It displays the total monthly WAN traffic and a list of client devices with their corresponding data usage.

### Usage

The PHP API provides four main endpoints to retrieve data. All endpoints return a JSON object.

#### `combined`

This is the main endpoint for a quick overview. It combines monthly totals for WAN traffic and all client traffic, using DHCP leases to provide hostnames.

* **URL:** `http://data.home/netstat/api.php?action=combined`

* **Example Output:**

    ```json
    {
      "wan_stats": {
        "rx_bytes": 10737418240,
        "tx_bytes": 5368709120,
        "last_update": "2025-08-02 23:00:00"
      },
      "client_stats": [
        {
          "rx_bytes": 1073741824,
          "tx_bytes": 536870912,
          "hostname": "my-laptop"
        },
        {
          "rx_bytes": 2147483648,
          "tx_bytes": 1073741824,
          "hostname": "my-phone"
        }
      ]
    }
    ```

#### `clients`

Returns the monthly traffic totals for all individual clients.

* **URL:** `http://data.home/netstat/api.php?action=clients`

#### `wan`

Returns the monthly traffic total for the main WAN interface.

* **URL:** `http://data.home/netstat/api.php?action=wan`

#### `leases`

Returns all active DHCP leases, as last collected by the Python script.

* **URL:** `http://data.home/netstat/api.php?action=leases`

### Contribution

Feel free to open an issue or submit a pull request if you find a bug or have a suggestion for an improvement.

### License

This project is licensed under the MIT License.
