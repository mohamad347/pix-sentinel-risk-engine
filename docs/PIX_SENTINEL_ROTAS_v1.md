# PIX SENTINEL — contrato HTTP implementado

**Atualizado em:** 18/10/2026
**Base URL local:** `http://localhost:3000`

## Rotas

| Método | Caminho | Autenticação | Sucesso |
|---|---|---|---|
| `GET` | `/` | pública | `200` com nome do serviço e PID. |
| `GET` | `/health_check` | pública | `204` sem corpo. |
| `POST` | `/pix/transaction` | `INTERNAL-TOKEN` | `200` com a decisão nova ou replay. |
| `GET` | `/pix/transaction/{id}` | `INTERNAL-TOKEN` | `200` com a decisão persistida. |

A documentação automática do FastAPI (`/docs`, `/redoc` e `/openapi.json`) está
desabilitada. Não há rota de execução de 2FA, confirmação, saldo ou liquidação.

O header opcional `X-Request-ID` aceita letras, números, `_` e `-`, com até 64
caracteres. Se ausente ou inválido, a API gera um UUID. O valor efetivo volta no
header de toda resposta.

## POST `/pix/transaction`

### Headers

```http
Content-Type: application/json
INTERNAL-TOKEN: default_token
```

O projeto espera JSON, mas não possui uma resposta `415` específica para media
type incorreto. JSON malformado ou corpo incompatível com a rota Pix é tratado
como payload inválido (`422`).

### Entrada

```json
{
  "id": "pix-000123",
  "amount": 150000,
  "transaction_date": "2026-10-17T22:15:00-03:00",
  "source_account": {
    "branch": "0001",
    "account_number": "88213",
    "opening_date": "2025-03-02T10:00:00-03:00",
    "owner": { "document_number": "123.456.789-09" }
  },
  "destination_account": {
    "participant": "60701190",
    "branch": "3675",
    "account_number": "10442",
    "opening_date": "2026-10-10T09:00:00-03:00",
    "owner": { "document_number": "987.654.321-00" }
  },
  "source": {
    "device_id": "dvc-9f2c71a8",
    "channel": "app",
    "ip": "198.51.100.23",
    "geolocation": { "latitude": -23.56, "longitude": -46.65 }
  }
}
```

### Regras de validação

| Campo | Regra efetiva |
|---|---|
| `id` | String não vazia, sem `/` e sem espaço no início/fim; não precisa ser UUID. |
| `amount` | Inteiro JSON estrito de 1 a `9223372036854775807`, em centavos; bool, float e string são inválidos. |
| `transaction_date` | ISO 8601 com `Z` ou offset; precisão máxima de microssegundos. |
| `opening_date` | Mesma exigência de fuso e precisão para origem e destino. |
| agência, conta, participante, documento | Strings não vazias; zeros são preservados. |
| `source.geolocation` | Opcional ou `null`; se presente, latitude `[-90,90]`, longitude `[-180,180]` e números finitos. |
| demais campos | `source.device_id`, `source.channel` e `source.ip` são strings obrigatórias não vazias. |

O schema usa `additionalProperties: true` em todos os níveis. Campos extras são
aceitos e persistidos, porém o fingerprint e a política ignoram o que não faz
parte do contrato oficial. O serviço não valida dígitos de CPF/CNPJ nem o
formato real do IP.

### Saída

O HTTP sempre é `200` quando a análise foi concluída, inclusive se o parecer é
`rejected`.

```json
{
  "key": "b3f1c2e0-7d4a-4c1e-9a52-1f0e6c9d2a77",
  "status": "two_factor_required",
  "metadata": {
    "schema_version": "1.0",
    "policy_version": "mvp-risk-adaptive-v3-r5",
    "reason_code": "ADAPTIVE_VERIFICATION_RECOMMENDED",
    "reason_description": "Independent risk contexts recommend additional verification.",
    "challenge": {
      "type": "push_confirmation",
      "level": "standard",
      "status": "recommended"
    }
  }
}
```

Campos públicos de primeiro nível:

| Campo | Tipo | Semântica |
|---|---|---|
| `key` | UUID em string | Identificador imutável da análise. |
| `status` | enum | `approved`, `two_factor_required` ou `rejected`. |
| `metadata` | objeto | Versão e motivo; em 2FA inclui o desafio recomendado. |

`challenge.type` pode ser `push_confirmation` ou, apenas para PF sob critérios
reforçados, `facial_liveness`. O status `recommended` deixa explícito que a API
não executou autenticação. Score, sinais, CPF/CNPJ, conta, IP e coordenadas não
são expostos.

### Idempotência

| Situação | Resposta |
|---|---|
| Primeiro POST válido | Cria tentativa e análise e retorna `200`. |
| Mesmo `id` e mesmos campos oficiais canônicos | Retorna exatamente o snapshot anterior com `200`; não reavalia. |
| Mesmo `id` e campo oficial divergente | Retorna `409` `QIT002004`; não sobrescreve. |
| Mudança apenas em campo extra | É replay, pois extras não entram no fingerprint. |
| Timestamp com outro offset, mas mesmo instante | É replay, pois o canonizador normaliza para UTC. |
| Geo omitida versus `null` | É replay; as duas representações são equivalentes. |

## GET `/pix/transaction/{id}`

`{id}` é o campo externo `id` enviado no POST, por exemplo `pix-000123`. Não é
o UUID `key` da análise.

```powershell
curl.exe -H "INTERNAL-TOKEN: default_token" http://localhost:3000/pix/transaction/pix-000123
```

Se encontrado, retorna `200` com os mesmos `key`, `status` e `metadata` do POST.
O endpoint não recalcula o score nem lê o histórico atual. Se não encontrado,
retorna `404` `QIT002002`.

## Erros

Todos os erros controlados seguem este envelope:

```json
{
  "title": "Pix transaction conflict",
  "description": "The Pix identifier is already associated with different transaction data.",
  "translation": "O identificador Pix já está associado a dados de transação diferentes.",
  "code": "QIT002004"
}
```

| HTTP | Código | Quando ocorre |
|---:|---|---|
| `403` | `QIT000002` | `INTERNAL-TOKEN` ausente ou diferente da configuração. |
| `404` | `QIT002002` | GET de um ID Pix sem análise persistida. |
| `404` | `QIT000404` | Caminho inexistente. |
| `405` | `QIT000405` | Método não permitido numa rota existente. |
| `409` | `QIT002004` | ID Pix já ligado a dados oficiais diferentes. |
| `422` | `QIT002001` | Corpo Pix inválido estrutural ou semanticamente. |
| `503` | `QIT002005` | Política solicitada ausente ou incapaz de avaliar evidência inválida. |
| `500` | `QIT000500` | Exceção inesperada, inclusive falha de banco não classificada. |

Não existe hoje um erro `503` específico para indisponibilidade do PostgreSQL,
nem `415` customizado. Esses comportamentos não devem ser presumidos por quem
integra.

## Exemplos de chamada

Com a API iniciada via Docker Compose, salve a entrada anterior em
`pix-request.json` e execute:

```powershell
curl.exe -X POST http://localhost:3000/pix/transaction `
  -H "Content-Type: application/json" `
  -H "INTERNAL-TOKEN: default_token" `
  --data-binary "@pix-request.json"
```

O token `default_token` é conveniência local do Compose. Em uma implantação
real, `INTERNAL_TOKEN` deve ser secreto e fornecido pelo ambiente.
