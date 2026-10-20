# ShellCrash Compatibility

ShellCrash's Linux firewall scripts include an outbound loop-prevention rule for socket group ID `7890`. Its standard `shellcrash` group uses that ID. X-Tunnel must run with primary group ID `7890` for its upstream sockets to match the rule; adding the group only as a supplementary group is not sufficient on this host.

On systemd Linux, the installer checks `/etc/group` for `shellcrash` with GID `7890`. When found, it installs managed `shellcrash-bypass.conf` drop-ins setting `Group=7890` for `x-tunnel.service`, `x-tunnel-speedtest.service`, and `x-tunnel-speedtest-single@.service`. When absent, it removes only those managed drop-ins and leaves the services on their default group. The speed-test code can then use its direct-route fallback when the ShellCrash group is unavailable.

On OpenWrt, the procd init scripts apply primary group `shellcrash` to the X-Tunnel supervisor and Bot only when `/etc/group` contains that name with GID `7890`. This lets X-Tunnel's upstream sockets use the native bypass without changing ShellCrash files or service state.

This integration changes only X-Tunnel systemd service settings. It does not read or write ShellCrash configuration, firewall rules, or service state, and it never starts, stops, or restarts ShellCrash. Re-run `install.sh` after installing ShellCrash later so the installer can detect GID `7890` and configure the drop-ins.

Verify the installed service group with:

```sh
systemctl show x-tunnel.service -p Group
systemctl show x-tunnel-speedtest.service -p Group
```

On a system with the standard ShellCrash group, both should report `Group=7890`. After restarting X-Tunnel, the process status should show `Gid: 7890 7890 7890 7890`:

```sh
pid=$(systemctl show -p MainPID --value x-tunnel.service)
grep '^Gid:' "/proc/$pid/status"
```
