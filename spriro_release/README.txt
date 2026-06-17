SPRIRO  -  ZWCAD Sprinkler Plugin
=================================

WHAT'S IN THIS FOLDER
  Spriro.dll            the plugin
  Newtonsoft.Json.dll   required library (must stay next to Spriro.dll)
  spriro.config.json    backend server address (edit if the server changes)
  README.txt            this file

Keep all four files together in one folder.


REQUIREMENTS
  - ZWCAD 2026 (Windows).
  - The Spriro backend must be running on the server. The plugin talks to it
    over the network using the address in spriro.config.json (currently
    146.190.72.89). The server must allow inbound TCP ports 9000-9002.


INSTALL  (once per ZWCAD session)
  1. In ZWCAD, type:  NETLOAD   and press Enter.
  2. Browse to this folder and select  Spriro.dll.
  3. The command line prints the loaded commands and the backend URLs.

  Tip: to auto-load on every start, run APPLOAD in ZWCAD, add Spriro.dll to the
  "Startup Suite".


COMMANDS  (type them on the ZWCAD command line, with the leading slash)
  /check          Pops up a window showing whether the backend is running.
                  Run this FIRST to confirm the connection.
  /sprinkler_p1   Place sprinklers - v1 (scenario picker).
  /sprinkler_p2   Place sprinklers - v2 (automatic / universal model).
  /routing        Route pipes through sprinklers that already exist in the
                  drawing.


CHANGING THE SERVER ADDRESS
  Open spriro.config.json in Notepad, change "host", save, then re-run NETLOAD
  (or restart ZWCAD):

      {
        "host": "146.190.72.89",
        "routing_port": 9000,
        "p1_port": 9001,
        "p2_port": 9002
      }

  For a backend running on the same PC, set   "host": "127.0.0.1".


TROUBLESHOOTING
  /check shows NOT RUNNING:
     - the backend is not running on the server, or
     - the server firewall is blocking ports 9000-9002, or
     - the address in spriro.config.json is wrong.
  NETLOAD fails / commands missing:
     - make sure Newtonsoft.Json.dll is in the same folder as Spriro.dll, and
     - if you copied the files from another PC, unblock them:
       right-click each file > Properties > tick "Unblock" > OK.
