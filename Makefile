.PHONY: build up down restart logs status health test

build:
	./scripts/manage.sh build

up:
	./scripts/manage.sh start

down:
	./scripts/manage.sh stop

restart:
	./scripts/manage.sh restart

logs:
	./scripts/manage.sh logs

status:
	./scripts/manage.sh status

health:
	./scripts/manage.sh health

test:
	PYTHONPATH=src python -m unittest discover -s tests -v
