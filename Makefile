.PHONY: help install test compile template clean

help:
	@echo "make install   - install Python dependencies"
	@echo "make test      - run unit tests"
	@echo "make compile   - byte-compile all Python scripts"
	@echo "make template  - generate a blank 37-column workbook"
	@echo "make clean     - remove Python caches (keeps audit artifacts)"

install:
	python3 -m pip install -r requirements.txt

test:
	python3 -m unittest discover -s tests -v

compile:
	python3 -m compileall scripts tests

template:
	python3 scripts/make_template.py --out 验收审核底表模板.xlsx

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
