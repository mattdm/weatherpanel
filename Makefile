# Deploy CircuitPython weather panel to mounted CIRCUITPY volume
# Compiles .py to .mpy or copies source, plus fonts and settings
MNT := /run/media/${USER}/CIRCUITPY
srcs := $(wildcard src/*.py)
fonts := $(wildcard fonts/*.pcf)
lib_files := $(shell find lib -type f 2>/dev/null)
CP_VERSION ?= $(shell cat .cp-version 2>/dev/null || grep -oP 'CircuitPython \K[0-9]+\.[0-9]+\.[0-9]+[^\s]*' ${MNT}/boot_out.txt 2>/dev/null)

all: deploy

${MNT}/%.mpy: src/%.py ${MNT}
	./bin/mpy-cross $< -o $@

# Debug: copy source instead of compiled .mpy (useful for serial tracebacks)
${MNT}/src/%.py: src/%.py ${MNT}
	@mkdir -pv ${MNT}/src
	@cp -v $< $@

${MNT}/fonts/%.pcf: fonts/%.pcf ${MNT}
	@mkdir -pv ${MNT}/fonts
	@cp -v $< $@

${MNT}/settings.toml: ${MNT} settings_real.toml
	@cp -v settings_real.toml ${MNT}/settings.toml

${MNT}/colors.toml: ${MNT} colors.toml
	@cp -v colors.toml $@

${MNT}/code.py: code.py
	@cp -v $^ $@

codepy: ${MNT}/code.py
settings: ${MNT}/settings.toml
colors: ${MNT}/colors.toml
mpys: $(srcs:src/%.py=${MNT}/%.mpy)
srcs: $(srcs:src/%.py=${MNT}/src/%.py)
fonts: $(fonts:fonts/%.pcf=${MNT}/fonts/%.pcf)

deploy: .lib-stamp codepy settings colors mpys srcs fonts
	@sync

clean:
	rm -I *.mpy
	rm -I ${MNT}/src/*
	rm -I ${MNT}/*.mpy


${MNT}:
	@echo Device not mounted at $@
	@false

# --- Device info ---
device-info: ${MNT}
	@cat ${MNT}/boot_out.txt 2>/dev/null || echo "No boot_out.txt found — device may need CircuitPython installed."

# --- Firmware update (interactive — delegates to script, then syncs mpy-cross) ---
update-firmware:
	./bin/update-firmware
	$(MAKE) --no-print-directory update-mpy-cross

# --- Download mpy-cross matching current .cp-version (or mounted device version) ---
update-mpy-cross:
	./bin/update-mpy-cross

# --- Refresh the repo-local lib/ cache via circup ---
update-libraries:
	@circup --version >/dev/null 2>&1 || { echo "circup not found or broken — run: pip install -r requirements-dev.txt"; false; }
	@test -n "$(CP_VERSION)" || { echo "CircuitPython version unknown. Run 'make update-firmware' first, or create .cp-version"; false; }
	circup --path . --cpy-version $(CP_VERSION) --board-id adafruit_matrixportal_s3 install -r circuitpython-requirements.txt --upgrade
	circup --path . --cpy-version $(CP_VERSION) --board-id adafruit_matrixportal_s3 install --py --upgrade adafruit_bitmap_font adafruit_display_text
	chmod -R g-s lib/

# --- Sync the current repo-local lib/ tree to the device ---
# .lib-stamp is a real file: Make only reruns rsync when lib/ contents change.
# 'make libs' is a PHONY alias that forces a sync regardless.
.lib-stamp: $(lib_files) circuitpython-requirements.txt | ${MNT}
	@command -v rsync >/dev/null 2>&1 || { echo "rsync not found — install rsync"; false; }
	@test -d lib || { echo "lib/ not populated — run: make update-libraries first"; false; }
	rsync -rl --delete --no-perms --no-owner --no-group lib/ ${MNT}/lib/
	@touch $@

libs: ${MNT}
	@rm -f .lib-stamp
	@$(MAKE) --no-print-directory .lib-stamp

.PHONY: all deploy clean device-info update-firmware update-mpy-cross update-libraries libs
