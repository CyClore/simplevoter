Architecture Overview & Design Notes

Allmon3's built-in voter.html page only supports hardware RTCM / chan_voter interfaces and cannot display
Simple Voter (usbradio RSSI-based voting) signal bars. This custom dashboard solves that limitation without
modifying AllStarLink core binaries.
• 
• 
• 
Frontend Web Dashboard (/var/www/html/simplevoter/index.html): A standalone dark-mode HTML/
JavaScript dashboard served alongside Allmon3. It polls status.json every 350ms to display 0–999 RSSI
signal bars, highlight the currently voted active receiver, and indicate receiver connection states.
Backend Collector Service (/usr/local/bin/simplevoter-mon.py): A multi-threaded Python service running via
systemd. It attaches to the local Asterisk CLI using a pseudo-terminal (PTY) to read local chan_usbradio
RSSI updates (Count_rssi_update) and remote voter messages in real time, while simultaneously polling
Asterisk Manager Interface (AMI) XStat and IAX2 text frames using Allmon3's existing credentials.
Atomic JSON State (/var/www/html/simplevoter/status.json): Written atomically every 250ms using a
temporary file swap so the web browser never reads a partial or locked file

make a folder on your webserver   
mkdir /var/www/html/simplevoter  and copy index.html in it.

then copy simplevoter-mon.py into /usr/local/bin/ 
chmod +x /usr/local/bin/simplevoter-mon.py

Install the systemd unit file so the monitor starts automatically on boot and restarts on any Asterisk reload

cat << 'EOF' > /etc/systemd/system/simplevoter-mon.service
[Unit]
Description=AllStarLink Simple Voter Web Monitor
After=network.target asterisk.service
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/simplevoter-mon.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now simplevoter-mon.service

That is it...  Pretty simple...
