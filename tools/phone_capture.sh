# shellcheck shell=bash
# Shared phone-capture steps; sourced after the wizard library and TOOLS_DIR.
phone_capture_guidance() {
  say "Optional: record the app-to-light traffic on the PHONE. Pi btmon records"
  say "only the Pi's connections and advertisements, not the phone's GATT exchange."
  step "Android: enable Developer options > Bluetooth HCI snoop log (Full if offered),"
  step "restart Bluetooth, then reproduce pairing and a colour change in the app."
  step "Take a bug report. Extract btsnoop_hci.log from its Bluetooth logs; the path"
  step "varies by phone. If absent, see tools/phone-capture.md for btsnooz extraction."
  step "iPhone: use Apple's Bluetooth logging profile and PacketLogger on a Mac"
  step "(New iOS Trace). See tools/phone-capture.md for Apple's current instructions."
  note "Keep the capture and bug report private; disable phone logging when finished."
}

phone_capture_analyse() {
  say "If you recorded phone traffic, copy the extracted capture to the Pi first."
  say "You can skip this and analyse it later: python3 tools/analyse_capture.py FILE"
  local capture_file=""
  ask capture_file "Capture path on this machine (Enter to skip):" || return 0
  [[ -n "$capture_file" ]] || return 0
  if [[ ! -f "$capture_file" ]]; then
    warn "Capture not found; copy it over and run the analysis command later."
    return 0
  fi
  "$PYTHON" "$TOOLS_DIR/analyse_capture.py" "$capture_file" || \
    warn "Capture analysis failed; see tools/phone-capture.md. The wizard can continue."
  pause "Capture inspected; continue?"
}
