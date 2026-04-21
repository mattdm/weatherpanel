# Deploy CircuitPython weather panel to mounted CIRCUITPY volume
# Compiles .py to .mpy or copies source, plus fonts and settings
MNT := /run/media/${USER}/CIRCUITPY
srcs := $(wildcard src/*.py)
fonts := $(wildcard fonts/*.pcf)
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

${MNT}/code.py: code.py
	@cp -v $^ $@

codepy: ${MNT}/code.py
settings: ${MNT}/settings.toml
mpys: $(srcs:src/%.py=${MNT}/%.mpy)
srcs: $(srcs:src/%.py=${MNT}/src/%.py)
fonts: $(fonts:fonts/%.pcf=${MNT}/fonts/%.pcf)

deploy: libs codepy settings mpys srcs fonts

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

# --- Firmware update (interactive — delegates to script) ---
update-firmware:
	./bin/update-firmware

# --- Refresh the repo-local lib/ cache via circup ---
update-libraries:
	@circup --version >/dev/null 2>&1 || { echo "circup not found or broken — run: pip install -r requirements-dev.txt"; false; }
	@test -n "$(CP_VERSION)" || { echo "CircuitPython version unknown. Run 'make update-firmware' first, or create .cp-version"; false; }
	circup --path . --cpy-version $(CP_VERSION) install -r circuitpython-requirements.txt --upgrade

# --- Sync the current repo-local lib/ tree to the device ---
libs: ${MNT}
	@command -v rsync >/dev/null 2>&1 || { echo "rsync not found — install rsync"; false; }
	@test -d lib || { echo "lib/ not populated — run: make update-libraries first"; false; }
	@mkdir -pv ${MNT}/lib
	rsync -a --delete lib/ ${MNT}/lib/

.PHONY: all deploy clean device-info update-firmware update-libraries libs
