# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

## Estado atual

A Fatia 2 implementa a infraestrutura persistente e idempotente do motor:
recebe um Pix, grava a tentativa e sua análise em uma única transação de banco,
recupera reenvios e permite consultar o parecer original pelo `id` externo.
Ainda não há regras reais de risco, velocity, score ou detecção de contraparte.

## Como o motor de decisão funciona

`POST /pix/transaction` valida o contrato oficial, preserva o payload recebido,
materializa os campos necessários às próximas consultas históricas e grava uma
linha em `pix_transactions` e outra em `pix_analyses`. O commit ocorre antes da
resposta HTTP. `GET /pix/transaction/{id}` lê o snapshot salvo, sem recalcular a
decisão.

Reenvios usam a canonização `pix-official-v1`. Ela considera somente os campos
oficiais, normaliza instantes equivalentes para UTC e preserva identificadores
textuais exatamente como recebidos. Campos extras são mantidos no primeiro
`request_payload`, mas não mudam a identidade. Um reenvio equivalente devolve a
decisão persistida; o mesmo `id` com dados oficiais diferentes devolve `409`.

Para atualizar uma base criada na Fatia 1, revise e execute manualmente
`database/002_add_pix_canonicalization_version.sql` no destino correto. O script
é incremental e não remove dados. Registros anteriores recebem a versão
`phase-1-provisional`; a comparação de replay recalcula a identidade a partir do
payload original para evitar conflitos falsos com o hash provisório.

Todas as rotas de negócio exigem o cabeçalho `INTERNAL-TOKEN`.

## Regras

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| Bootstrap técnico | Retorna `approved` para um payload válido, deixando explícito que não houve avaliação de risco | Versão `bootstrap-dev-v1`; habilitada somente em `local`, `test` ou `development` | Permitir validar o fluxo técnico antes da aprovação da política antifraude |

A regra bootstrap é provisória e não representa um motor antifraude concluído.
Em outros ambientes a API responde com indisponibilidade da política em vez de
aprovar sem uma política habilitada.

## Como as regras viram decisão

Nesta fatia existe apenas a decisão bootstrap. Os três pareceres e sua composição
serão implementados depois que sinais e parâmetros forem aprovados.

## Como rodar

Para uma instalação nova, o Compose inicializa o PostgreSQL com
`database/database.sql`:

```bash
docker compose up --build
```

O comando acima usa `APP_ENV=local` e `INTERNAL_TOKEN=default_token` por padrão.
Não aplique o arquivo de inicialização sobre um banco existente sem revisar uma
atualização incremental própria para esse ambiente.

## Como testar

Os testes unitários não acessam o banco:

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/unit -q
```

Os testes de integração Pix exigem uma API ativa e um PostgreSQL descartável. Há
duas travas deliberadas: `PIX_INTEGRATION_TESTS=1` e o nome do banco em
`DATABASE_URL` precisa conter `test`.

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build -d
$env:DATABASE_URL = "postgresql+psycopg2://pix_test:pix_test@localhost:55432/pix_sentinel_test"
$env:PIX_INTEGRATION_TESTS = "1"
$env:APP_ENV = "test"
$env:PYTHONPATH = "src"
$env:SERVER_LOCALHOST = "localhost"
$env:API_PORT = "3300"
$env:INTERNAL_TOKEN = "pix-test-token"
python -m pytest tests/integration/pix -q
```

O Compose de testes usa nomes, portas e credenciais próprias. O diretório de
dados do PostgreSQL fica em `tmpfs`, sem reutilizar volumes de desenvolvimento.

## Exemplo de POST

```bash
curl -X POST http://localhost:3000/pix/transaction \
  -H "Content-Type: application/json" \
  -H "INTERNAL-TOKEN: default_token" \
  -d '{
    "id": "pix-000123",
    "amount": 150000,
    "transaction_date": "2026-10-17T22:15:00-03:00",
    "source_account": {
      "branch": "0001",
      "account_number": "88213",
      "opening_date": "2025-03-02T10:00:00-03:00",
      "owner": {"document_number": "123.456.789-09"}
    },
    "destination_account": {
      "participant": "60701190",
      "branch": "3675",
      "account_number": "10442",
      "opening_date": "2026-10-10T09:00:00-03:00",
      "owner": {"document_number": "987.654.321-00"}
    },
    "source": {
      "device_id": "dvc-9f2c71a8",
      "channel": "app",
      "ip": "198.51.100.23"
    }
  }'
```

## Exemplo de GET

```bash
curl http://localhost:3000/pix/transaction/pix-000123 \
  -H "INTERNAL-TOKEN: default_token"
```
