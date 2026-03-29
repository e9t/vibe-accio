# accio

Watches a folder and automatically renames academic PDFs to `Author - Year - Title.pdf`.

> Part of the [lumos](https://github.com/lucypark/vibe-lumos) personal knowledge toolkit.

## Metadata resolution order

1. arXiv ID (extracted from filename or first-page text)
2. Semantic Scholar API
3. PDF embedded metadata
4. Text extraction fallback

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Default (watches ~/papers → saves to ~/papers_renamed)
python accio.py

# Custom paths
python accio.py -i ~/Downloads/papers -o ~/Library/Papers

# Also process existing PDFs on startup
python accio.py -i ~/papers -o ~/renamed --process-existing
```

## Run in background (macOS launchd)

`~/Library/LaunchAgents/com.lucypark.accio.plist` 생성:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lucypark.accio</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/lucypark/dev/my/vibe-accio/accio.py</string>
        <string>-i</string>
        <string>/Users/lucypark/papers</string>
        <string>-o</string>
        <string>/Users/lucypark/papers_renamed</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/accio.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/accio.err</string>
</dict>
</plist>
```


```bash
launchctl unload ~/Library/LaunchAgents/com.lucypark.accio.plist
launchctl load ~/Library/LaunchAgents/com.lucypark.accio.plist
```
