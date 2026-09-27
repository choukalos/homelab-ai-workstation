#!/usr/bin/env bash
# backup-setup.sh — ONE-TIME root setup for the Lego backup mount.
#
#   sudo /home/chuck/homelab/scripts/backup/backup-setup.sh <share-name>
#
# What it does (idempotent — safe to re-run after changing share/password):
#   1. verifies /home/chuck/.smbcredentials has a real password
#   2. writes the share name into backup.conf
#   3. creates /mnt/lego/homelab
#   4. installs the fstab entry:  //lego.local/<share>  /mnt/lego  cifs
#      credentials=...,vers=2.1,soft,noauto,x-systemd.automount,_netdev
#      (noauto + automount: a downed NAS can't wedge boot or the backup script)
#   5. mounts, creates the backup tree, checks free capacity, writes a test file

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=backup.conf
source "$SCRIPT_DIR/backup.conf"

SHARE="${1:-}"
[[ -n "$SHARE" ]] || { echo "usage: sudo $0 <share-name>"; exit 1; }
[[ "$SHARE" =~ ^[A-Za-z0-9_-]{1,63}$ ]] || { echo "invalid share name: $SHARE (allowed: letters, digits, -, _)"; exit 1; }
[[ $EUID -eq 0 ]] || { echo "must run as root (sudo)"; exit 1; }

MARKER="# lego-backup-managed"

fail() { echo "SETUP FAILED: $*" >&2; exit 1; }

# 1. credentials
[[ -f "$CRED_FILE" ]] || fail "credentials file $CRED_FILE missing — create it (username=/password=, chmod 600)"
if grep -qE '^password=CHANGE_ME' "$CRED_FILE"; then
  fail "$CRED_FILE still contains the CHANGE_ME placeholder — set the real Lego password first"
fi

# 2. share name in conf
sed -i "s|^LEGO_SHARE=.*|LEGO_SHARE=\"$SHARE\"|" "$SCRIPT_DIR/backup.conf"
echo "backup.conf: LEGO_SHARE=\"$SHARE\""

# 3. mount point + per-host backup root (e.g. /mnt/lego/matrix)
# Self-heal first: Lego's macOS SMB server kills idle CIFS sessions quickly
# and rate-limits after churn, so a previous mount may be hung. Both recovery
# steps are bounded (see ensure_mounted in backup-lib.sh for the full story):
# umount -l can hang while procs hold I/O to a dead superblock (reboot clears
# it), and the server's rate-limit can make mount take minutes.
if ! timeout 15 ls "$MOUNT" >/dev/null 2>&1; then
  echo "mount of $MOUNT missing or unresponsive — (re)mounting"
  timeout 30 umount -l "$MOUNT" 2>/dev/null || true
  sleep 1
  if grep -qE "^[^ ]+ $MOUNT " /proc/mounts 2>/dev/null; then
    fail "stale mount of $MOUNT still attached (umount -l timed out) — wedged CIFS state, reboot the host to clear it"
  fi
  timeout 600 mount "$MOUNT" || fail "mount failed after 10 min — check share name, credentials, and network (lego.local -> $LEGO_IP)"
  timeout 15 ls "$MOUNT" >/dev/null 2>&1 || fail "$MOUNT still unresponsive after remount — check the NAS (dmesg | grep -i cifs)"
fi
mkdir -p "$BACKUP_ROOT"
chmod 755 "$MOUNT" "$BACKUP_ROOT"

echo "layout: $LEGO_HOST share '$SHARE' -> $MOUNT ; this host's tree: $BACKUP_ROOT"

# 4. fstab (replace any previous managed entry)
# uid=/gid= make the root-mounted share appear owned by the backup user.
# NO file_mode/dir_mode: Lego's macOS SMB server doesn't expose real Unix
# modes anyway (client reports 755 for everything), and a forced file_mode
# breaks rsync --link-dest hardlinking (permission mismatch -> full copies).
# The backup scripts therefore use rsync -rt (no permission preservation).
# `soft` (not `hard`): Lego's SMB server kills idle sessions and rate-limits
# after churn; a dead session on a hard mount blocks I/O forever and wedges
# the kernel (zombie D-state procs + stuck umount -l). With soft, I/O fails
# with an error instead, the verify step catches partial snapshots, and the
# next run resumes (hardlink rotation keeps re-runs cheap).
FSTAB_LINE="//$LEGO_HOST/$SHARE	$MOUNT	cifs	credentials=$CRED_FILE,uid=$MOUNT_UID,gid=$MOUNT_GID,vers=2.1,soft,noauto,x-systemd.automount,_netdev	0 0"
# NOTE: vers=2.1 — Lego's macOS SMB server rejects SMB3 negotiation (EOPNOTSUPP)
# and SMB2.0.2 (EINVAL); 2.1 is the highest dialect it accepts (tested 2026-09-26).
if grep -qF "$MARKER" /etc/fstab; then
  sed -i "/$MARKER/d; /^\/\/$LEGO_HOST/d" /etc/fstab
  echo "fstab: removed previous managed entry"
fi
printf '%s\n%s\n' "$MARKER" "$FSTAB_LINE" >> /etc/fstab
echo "fstab: installed $FSTAB_LINE"

# 4b. scoped sudoers: let the backup user (re)mount $MOUNT without a password.
#     Needed for self-healing: Lego's macOS SMB server drops idle CIFS sessions
#     after ~3 minutes; the backup script lazy-remounts via this rule.
BACKUP_USER=$(getent passwd "$MOUNT_UID" | cut -d: -f1)
[[ -n "$BACKUP_USER" ]] || fail "could not resolve username for uid $MOUNT_UID"
SUDOERS_FILE=/etc/sudoers.d/lego-backup
printf '%s ALL=(root) NOPASSWD: /bin/mount %s, /bin/umount -l %s\n' "$BACKUP_USER" "$MOUNT" "$MOUNT" > "$SUDOERS_FILE"
chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1 || { rm -f "$SUDOERS_FILE"; fail "sudoers validation failed"; }
echo "sudoers: $BACKUP_USER can mount/remount $MOUNT without password"

# 4b2. privileged rsync wrapper + its own scoped sudoers file (kept separate
#      from the mount rule above so a bad edit can't take the mount rule down).
#      Root rsync reads everything under /home/chuck/data — ComfyUI runs as
#      uid 1024 in-container and saves some files 0600, unreadable by the
#      backup user (broke the 2026-09-27 routine run, rsync code 23). The
#      wrapper (root-owned, fixed rsync flags, src/dest root validation) is
#      the only enforcement; the sudoers rule grants no arguments.
install -m 755 -o root -g root "$SCRIPT_DIR/lego-backup-rsync" /usr/local/sbin/lego-backup-rsync
RSYNC_SUDOERS_FILE=/etc/sudoers.d/lego-backup-rsync
printf '%s\n%s\n' "# lego-backup: privileged rsync wrapper (see /usr/local/sbin/lego-backup-rsync)" "$BACKUP_USER ALL=(root) NOPASSWD: /usr/local/sbin/lego-backup-rsync" > "$RSYNC_SUDOERS_FILE"
chmod 440 "$RSYNC_SUDOERS_FILE"
visudo -c -f "$RSYNC_SUDOERS_FILE" >/dev/null 2>&1 || { rm -f "$RSYNC_SUDOERS_FILE"; fail "sudoers validation failed (rsync wrapper)"; }
echo "sudoers: $BACKUP_USER can run the privileged rsync wrapper without password"

# 4c. systemd: re-run the fstab generator so the x-systemd.automount unit
#     exists without a reboot (the generator normally runs at boot only).
systemctl daemon-reload 2>/dev/null && echo "systemd: fstab units regenerated (lego.automount)" || echo "WARNING: systemctl daemon-reload failed (units appear after reboot)"

# 5. mount + verify
# ensure the cifs kernel module is present (auto-loads on mount as root,
# but be explicit — the module index must be current: depmod -a if not)
lsmod | grep -q '^cifs ' || modprobe cifs || echo "WARNING: could not load cifs module (try: sudo depmod -a && sudo modprobe cifs)"
if timeout 15 mountpoint -q "$MOUNT"; then
  echo "remounting (was already mounted)..."
  umount "$MOUNT" 2>/dev/null || umount -l "$MOUNT"
fi
mount "$MOUNT" || fail "mount failed — check share name, credentials, and network (lego.local -> $LEGO_IP)"
echo "mounted $MOUNT"

mkdir -p "$BACKUP_ROOT/routine" "$BACKUP_ROOT/models"

# capacity check (plan: verify >= ~150 GB free)
avail_kb=$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {print $4}')
avail_gb=$((avail_kb / 1024 / 1024))
if (( avail_gb < 100 )); then
  fail "only ${avail_gb}GB free on the share — model track needs ~120GB; free up space or reduce scope"
elif (( avail_gb < 150 )); then
  echo "WARNING: ${avail_gb}GB free — plan target was >= ~150GB (110GB models + snapshots + growth)"
else
  echo "capacity OK: ${avail_gb}GB free"
fi

# write test
testfile="$BACKUP_ROOT/.write-test-$$"
echo "lego-backup setup $(date -Is)" > "$testfile"
rm -f "$testfile"
echo "write test OK"

echo
echo "=== setup complete ==="
echo "Next steps:"
echo "  1. /home/chuck/homelab/scripts/backup/backup-routine.sh --dry-run   # sanity check"
echo "  2. /home/chuck/homelab/scripts/backup/backup-routine.sh             # first real snapshot"
echo "  3. /home/chuck/homelab/scripts/backup/backup-models.sh --dry-run    # then the model set"