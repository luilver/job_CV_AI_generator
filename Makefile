.PHONY: setup install run restart test clean

# Detect native Windows (CMD/PowerShell) and block it with a helpful message
ifeq ($(OS),Windows_NT)
    # Check if SHELL is a Unix-like shell (bash, sh, zsh)
    # Git Bash and WSL set SHELL to something containing "bash" or "sh"
    # Native CMD/PowerShell does NOT.
    ifeq (,$(or $(findstring bash,$(SHELL)),$(findstring sh,$(SHELL)),$(findstring zsh,$(SHELL))))
        $(error Native Windows (CMD/PowerShell) is not supported. Please use WSL (Windows Subsystem for Linux) or Git Bash.)
    endif
endif

VENV=.venv
PYTHON=$(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@if [ ! -f credentials.json ]; then cp credentials.json.example credentials.json; fi

install:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) -m streamlit run landing.py

restart:
	@pkill -f "streamlit run" 2>/dev/null || true
	@sleep 1
	$(PYTHON) -m streamlit run landing.py

test:
	$(PYTHON) -m pytest tests

clean:
	rm -rf __pycache__ .pytest_cache $(VENV) *.pyc
