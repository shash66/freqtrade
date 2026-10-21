.PHONY: up down logs build test test-backend test-frontend test-contracts admin-hash migrate lint
up:            ## Build and start everything
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f --tail=200
build:
	docker compose build
migrate:
	docker compose run --rm migrate
admin-hash:    ## make admin-hash PASSWORD='...'  -> prints ADMIN_PASSWORD_HASH value
	@docker compose run --rm -T api python -c "from app.core.security import hash_password; print(hash_password('$(PASSWORD)'))" | sed 's/\$$/$$$$/g'
test: test-backend test-contracts
test-backend:
	cd backend && python -m pytest -q
test-frontend:
	cd frontend && npx playwright test
test-contracts:
	cd contracts && forge test -vvv
lint:
	cd backend && ruff check . && cd ../frontend && npm run lint
