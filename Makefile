# Deploy CircuitPython weather panel to mounted CIRCUITPY volume
# Compiles .py to .mpy or copies source, plus fonts and settings
MNT := /run/media/${USER}/CIRCUITPY
srcs := $(wildcard src/*.py)
fonts := $(wildcard fonts/*.pcf)

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

deploy: codepy settings mpys srcs fonts

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

# --- Library update via circup ---
update-libraries: ${MNT}
	@command -v circup >/dev/null || { echo "circup not found — run: pip install -r requirements-dev.txt"; false; }
	circup --path ${MNT} install -r circuitpython-requirements.txt --upgrade

# --- Convenience combo: update libraries then deploy app code ---
update: update-libraries deploy

.PHONY: all deploy clean device-info update-firmware update-libraries update
