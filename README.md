# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

## Estado atual

A Fatia 1 implementa a infraestrutura persistente do motor: recebe um Pix,
grava a tentativa e sua análise em uma única transação de banco e permite
consultar o parecer original pelo `id` externo. Ainda não há regras reais de
risco, velocity, score ou detecção de contraparte.

## Como o motor de decisão funciona

`POST /pix/transaction` valida o contrato oficial, preserva o payload recebido,
materializa os campos necessários às próximas consultas históricas e grava uma
linha em `pix_transactions` e outra em `pix_analyses`. O commit ocorre antes da
resposta HTTP. `GET /pix/transaction/{id}` lê o snapshot salvo, sem recalcular a
decisão.

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
$env:PIX_INTEGRATION_TESTS = "1"
$env:APP_ENV = "test"
$env:PYTHONPATH = "src"
python -m pytest tests/integration/pix -q
```

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
