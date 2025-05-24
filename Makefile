MNT := /run/media/${USER}/CIRCUITPY
srcs := $(wildcard src/*.py)
docs := $(wildcard *.txt) $(wildcard *.md)
fonts := $(wildcard fonts/*.pcf)
images := $(wildcard images/*.bmp)

all: deploy

${MNT}/%.mpy: src/%.py ${MNT}
	./bin/mpy-cross $< -o $@

${MNT}/src/%.py: src/%.py ${MNT}
	@mkdir -pv ${MNT}/src
	@cp -v $< $@

${MNT}/%.txt: %.txt ${MNT}
	@cp -v $< $@

${MNT}/%.md: %.md ${MNT}
	@cp -v $< $@
	
${MNT}/fonts/%.pcf: fonts/%.pcf ${MNT}
	@mkdir -pv ${MNT}/fonts
	@cp -v $< $@

${MNT}/images/%.bmp: images/%.bmp ${MNT}
	@mkdir -pv ${MNT}/images
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
images: $(images:images/%.bmp=${MNT}/images/%.bmp)
docs: $(docs:%.txt=${MNT}/%.txt) $(docs:%.md=${MNT}/%.md)

deploy: codepy settings mpys srcs docs fonts images

clean: 
	rm -I *.mpy
	rm -I ${MNT}/src/*
	rm -I ${MNT}/*.mpy


${MNT}:
	@echo Device not mounted at $@
	@false

