# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

O PIX SENTINEL é um motor antifraude transacional para solicitações Pix ainda
não executadas. Ele recebe os dados oficiais do Pix, consulta o histórico local
de tentativas da conta pagadora e devolve um parecer explicável:
`approved`, `two_factor_required` ou `rejected`.

O parecer não consulta saldo, não movimenta dinheiro, não comprova fraude e não
executa autenticação adicional.

## Arquitetura

O projeto preserva o padrão do Base Service:

```text
HTTP → Resource → Controller → Repository → SQLAlchemy Model → PostgreSQL
```

- `src/resources/` expõe o contrato HTTP;
- `src/controllers/` coordena validação, idempotência, histórico e política;
- `src/repositories/` concentra locks e consultas PostgreSQL;
- `src/models/` mapeia `pix_transactions` e `pix_analyses`;
- `src/schemas/` valida o payload oficial com JSON Schema;
- `src/errors/` mantém o envelope QI Tech;
- `tests/unit/` testa contrato e política sem banco;
- `tests/integration/` prova persistência e concorrência no PostgreSQL real.

As rotas Pix exigem `INTERNAL-TOKEN`. A raiz e `/health_check` permanecem
públicas para infraestrutura.

## Como o motor de decisão funciona

`POST /pix/transaction` valida o JSON oficial e sua semântica. Um reenvio com o
mesmo `id` e payload canônico devolve a análise original; o mesmo `id` associado
a dados oficiais diferentes retorna `409`.

Para uma tentativa nova, o motor:

1. adquire um `pg_advisory_xact_lock` pela composição exata de documento,
   agência e conta do pagador;
2. registra `received_at` com `clock_timestamp()` do PostgreSQL;
3. persiste a tentativa e extrai frequência e montante tentado nas janelas de
   5 e 60 minutos;
4. aplica a política determinística `mvp-risk-experimental-v1`;
5. persiste tentativa, evidências e decisão no mesmo commit;
6. retorna somente `key`, `status` e `metadata`.

As janelas usam `(T-W, T]` e incluem a tentativa atual uma vez. Todos os
pareceres persistidos representam tentativas observadas; nenhum deles comprova
liquidação. `GET /pix/transaction/{id}` recupera o snapshot original pelo ID
externo, sem recalcular a política.

## Regras

Os valores abaixo são parâmetros experimentais para demonstração. Eles não
foram calibrados em dados reais e não constituem política de produção.

| Regra | Hipótese | Condição | Resultado | Justificativa |
|---|---|---|---|---|
| Valor elevado (P1) | Uma tentativa isolada pode concentrar exposição | `amount >= 500_000` centavos (R$ 5.000) | `two_factor_required` | Recomenda confirmação proporcional; valor sozinho nunca rejeita |
| Frequência elevada (P2) | Ritmo alto pode indicar automação ou uso anômalo | `attempt_count_5m >= 4`, incluindo a atual | `two_factor_required` | Detecta concentração temporal, com risco conhecido de fricção legítima |
| Montante acumulado (P3) | Tentativas moderadas podem acumular exposição | `amount_cents_60m >= 1_500_000` (R$ 15.000) | `two_factor_required` | Soma centavos inteiros e captura exposição agregada |
| Contagem crítica (P4) | Rajada extrema eleva o risco | `attempt_count_5m >= 8` | Somente compõe a rejeição | Contagem isolada ainda pode ser uso legítimo intenso |
| Volume crítico (P5) | Uma rajada pode concentrar grande exposição | `amount_cents_5m >= 3_000_000` (R$ 30.000) **junto de P4** | `rejected` | A rejeição exige P4 e P5; a combinação continua heurística e correlacionada |

## Como as regras viram decisão

A precedência é determinística:

1. `rejected` / `SUSPICIOUS_BURST_AND_VOLUME`: contagem de 5 minutos maior ou
   igual a P4 **e** montante de 5 minutos maior ou igual a P5.
2. `two_factor_required` / `ADDITIONAL_VERIFICATION_RECOMMENDED`: pelo menos
   uma condição P1, P2 ou P3. O motor recomenda 2FA, mas não o executa.
3. `approved` / `RISK_WITHIN_EXPERIMENTAL_POLICY`: nenhuma condição anterior.

Exemplos:

- primeira tentativa de R$ 100: `approved`;
- primeira tentativa de R$ 5.000: `two_factor_required`;
- oitava tentativa em 5 minutos, totalizando R$ 30.000: `rejected`;
- uma transferência pontual de R$ 30.000: `two_factor_required`, nunca rejeição
  apenas pelo valor.

Cold start não adiciona risco por si só: ausência de histórico local não prova
que seja o primeiro Pix bancário do cliente. PF e PJ usam os mesmos parâmetros,
pois o motor não possui cadastro ou perfil econômico que sustente segmentação.
Essa escolha pode gerar fricção em lotes legítimos de uma PJ e está registrada
como limitação, não como comportamento ideal comprovado.

O `metadata` público contém somente versão e motivo. O snapshot interno
`pix-history-v1` registra contagens, somas em centavos, instante de admissão e
códigos dos sinais acionados, sem documento, conta, IP ou geolocalização.

### Contexto local de contraparte

Para novas análises, o snapshot contém a seção interna `pix-counterparty-v1`.
A contraparte é comparada pela composição exata de participante, agência, conta
e documento do destinatário, sempre dentro do histórico da conta pagadora.
Nenhum desses identificadores é copiado para o snapshot ou para o `metadata`
público.

O contexto usa três estados:

- `insufficient_history`: o PIX SENTINEL ainda não observou tentativa anterior
  do pagador e, portanto, não afirma que o destinatário seja novo;
- `observed`: existe ao menos uma tentativa anterior do mesmo pagador para a
  mesma contraparte;
- `not_observed`: existe histórico anterior do pagador, mas nenhuma tentativa
  anterior para a contraparte atual.

A tentativa atual é sempre excluída da contagem de contraparte. Replays não
criam observações nem alteram snapshots persistidos.

A matriz executada de hipóteses, decisões e falhas está em
[`docs/PIX_SENTINEL_MATRIZ_NEGOCIO_E_RESILIENCIA.md`](docs/PIX_SENTINEL_MATRIZ_NEGOCIO_E_RESILIENCIA.md).

### Avaliação de uma política v2

A política ativa continua sendo `mvp-risk-experimental-v1`. Existe uma candidata
`mvp-risk-contextual-v2-candidate`, restrita a seleção explícita com
`APP_ENV=test`. Ela calcula score interno 0–100 usando exposição, perfil,
contraparte/diversidade e dispositivo; geo e PF/PJ permanecem diagnósticos.
Replays antigos nunca são reavaliados.

Para um ambiente de teste isolado:

```powershell
$env:PIX_RISK_POLICY = "mvp-risk-contextual-v2-candidate"
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build -d --force-recreate pix_test_api
```

Não use essa configuração como ativação produtiva. O relatório A/B2/C2 concluiu
NO-GO para substituir V1; a amostra sintética é pequena e há divergências que
precisam de revisão.

| Intenção sintética | Contexto | Resultado v1 | Risco de erro observado |
|---|---|---|---|
| Primeira tentativa moderada | Histórico insuficiente | `approved` | Pode aprovar risco não visível no histórico local |
| Transferência única de R$ 5.000 | Histórico insuficiente | `two_factor_required` | Possível fricção em transferência legítima |
| Quarta tentativa pequena para a mesma contraparte | Observada | `two_factor_required` | Possível fricção em pagamentos legítimos repetidos |
| Quarta tentativa pequena para destinatários diferentes | Não observada | `two_factor_required` | Contexto sugere dispersão, mas não prova fraude |
| Oito tentativas e R$ 30.000 em 5 minutos | Observada ou não observada | `rejected` | Pode rejeitar uma rajada legítima; combinação continua experimental |

Esses rótulos descrevem intenção de cenários sintéticos, não verdade sobre
fraudes reais. A V2 não foi adotada e não há alegação de redução produtiva de
falso positivo ou falso negativo.

## Contrato HTTP

### POST `/pix/transaction`

Recebe o payload oficial e retorna HTTP `200` somente após o commit das duas
tabelas. A chave pública é o UUID da análise e não o `id` externo do Pix.
Reenvio canonicamente idêntico retorna exatamente a decisão persistida; o mesmo
ID com conteúdo oficial diferente retorna `409 QIT002004`.

Respostas sintéticas possíveis:

```json
{"key":"af66afaf-1a4a-4d94-9d55-00567fb9b21c","status":"approved","metadata":{"schema_version":"1.0","policy_version":"mvp-risk-experimental-v1","reason_code":"RISK_WITHIN_EXPERIMENTAL_POLICY","reason_description":"Risk is within the experimental antifraud policy."}}
```

```json
{"key":"0210407f-f69b-475d-9af6-88a039acbe27","status":"two_factor_required","metadata":{"schema_version":"1.0","policy_version":"mvp-risk-experimental-v1","reason_code":"ADDITIONAL_VERIFICATION_RECOMMENDED","reason_description":"Additional verification is recommended by the experimental antifraud policy."}}
```

```json
{"key":"37e93fa8-f942-4b68-87f9-78d8a91b2657","status":"rejected","metadata":{"schema_version":"1.0","policy_version":"mvp-risk-experimental-v1","reason_code":"SUSPICIOUS_BURST_AND_VOLUME","reason_description":"The request matched the experimental critical-risk combination."}}
```

### GET `/pix/transaction/{id}`

Pesquisa pelo `id` externo recebido no POST e devolve a mesma estrutura pública
persistida, sem reavaliar regras. ID inexistente retorna `404 QIT002002`.

Erros usam o envelope QI Tech:

```json
{
  "title": "Pix transaction conflict",
  "description": "The Pix identifier is already associated with different transaction data.",
  "translation": "O identificador Pix já está associado a dados de transação diferentes.",
  "code": "QIT002004"
}
```

## Como rodar

Uma instalação nova é inicializada por `database/database.sql`:

```bash
docker compose up --build -d
docker compose ps
```

A API atende em `http://localhost:3000`. As rotas Pix exigem:

```text
INTERNAL-TOKEN: default_token
```

Para bancos criados em fatias anteriores, revisar e aplicar no destino correto:

- `database/002_add_pix_canonicalization_version.sql`;
- `database/003_add_pix_payer_history_index.sql`.

Esses arquivos são incrementais. Não aplique `database/database.sql` sobre uma
base existente.

## Como testar

Em um ambiente virtual com `requirements-dev.txt` instalado, o comando padrão
funciona diretamente da raiz:

```powershell
python -m pytest -q
```

Sem opt-in, testes que exigem API/PostgreSQL são ignorados de forma explícita.
Para executar a suíte completa no ambiente descartável e isolado:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build -d

$env:APP_ENV = "test"
$env:DATABASE_URL = "postgresql+psycopg2://pix_test:pix_test@localhost:55432/pix_sentinel_test"
$env:PIX_INTEGRATION_TESTS = "1"
$env:SERVER_LOCALHOST = "localhost"
$env:API_PORT = "3300"
$env:INTERNAL_TOKEN = "pix-test-token"

python -m pytest -q
```

O Compose de testes usa banco, credenciais e portas próprias. Os dados do
PostgreSQL ficam em `tmpfs`, sem reutilizar volumes de desenvolvimento.

Validação final em 18/10/2026, Windows e Docker Desktop, com a API em
`APP_ENV=production` e PostgreSQL 16 isolado:

| Comando | Tipo | Resultado | Tempo pytest |
|---|---|---:|---:|
| `python -m pytest -q tests/unit` | Unitário | 91 passed | 2,55 s |
| `python -m pytest -q` com opt-in e API V1 | Suíte de produto/PostgreSQL | 119 passed, 3 skipped | 8,46 s |
| testes V2 e concorrência selecionados | PostgreSQL/API V2 | 6 passed | 4,32 s |
| `python -m pytest -q` sem opt-in | Comando padrão | 91 passed, 31 skipped | 4,13 s |

Todos terminaram sem falhas. Os três skips no modo integrado V1 são os fluxos
que exigem seleção explícita da V2; os demais skips do comando padrão exigem
PostgreSQL/API. O único aviso é a API depreciada `declarative_base()` do
SQLAlchemy. `pytest.ini` restringe a coleta a `tests/`; testes históricos
guardados em documentos não entram na contagem.

Medições E2E sequenciais comparáveis, cada uma com 100 payloads sintéticos e
pagadores distintos, obtiveram:

| Versão | Rota | p50 | p95 | p99 |
|---|---|---:|---:|---:|
| Antes do contexto | POST `/pix/transaction` | 75,18 ms | 111,65 ms | 148,35 ms |
| Com `pix-counterparty-v1` | POST `/pix/transaction` | 74,05 ms | 108,87 ms | 133,18 ms |
| Antes do contexto | GET `/pix/transaction/{id}` | 9,95 ms | 15,31 ms | 19,52 ms |
| Com `pix-counterparty-v1` | GET `/pix/transaction/{id}` | 9,84 ms | 14,36 ms | 22,82 ms |

Esses números descrevem apenas o ambiente local de teste; não são benchmark de
produção nem estimativa de capacidade sob concorrência.

Uma medição concorrente controlada com 80 POSTs, 8 workers, IDs e pagadores
distintos obteve p50 285,24 ms, p95 343,95 ms, p99 353,43 ms e máximo 362,93 ms.
Ela também é apenas evidência local, sem valor de SLA.

O advisory lock por pagador usa `lock_timeout=1000ms`. Se a contenção exceder
esse limite, a transação falha tecnicamente e sofre rollback; o cliente pode
reconciliar reenviando o mesmo `external_id` e payload.

## Demonstração reproduzível

Com a API ativa, execute:

```powershell
python scripts/demo_pix_sentinel.py --base-url http://localhost:3000 --token default_token
```

O script usa somente a biblioteca padrão e dados sintéticos. Ele cria IDs
distintos e comprova os três pareceres, GET, replay com a mesma resposta e
conflito `409`. Na auditoria final, gerou exatamente 10 linhas em cada tabela:
uma aprovação, uma recomendação por alto valor e oito tentativas da rajada; GET,
replay e conflito não criaram registros adicionais.

## Exemplo de requisição

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
      "account_number": "00088213",
      "opening_date": "2025-03-02T10:00:00-03:00",
      "owner": {"document_number": "00000000000"}
    },
    "destination_account": {
      "participant": "060701190",
      "branch": "03675",
      "account_number": "00010442",
      "opening_date": "2026-10-10T09:00:00-03:00",
      "owner": {"document_number": "11111111111"}
    },
    "source": {
      "device_id": "device-demo",
      "channel": "app",
      "ip": "198.51.100.23"
    }
  }'
```

Consulta do snapshot persistido:

```bash
curl http://localhost:3000/pix/transaction/pix-000123 \
  -H "INTERNAL-TOKEN: default_token"
```

## Limitações

- O histórico contém somente tentativas válidas observadas pelo PIX SENTINEL.
- `is_first_observed_attempt` não significa que o cliente nunca realizou Pix.
- Contagens e somas representam tentativas, não operações liquidadas.
- O contexto de contraparte é apenas histórico local observado; não representa
  reputação externa nem prova que um destinatário seja legítimo ou fraudulento.
- Não há perfil bancário cadastrado nem detecção enriquecida de contraparte.
- Device, IP e geolocalização são declarados pelo payload e não autenticam o
  titular.
- Os parâmetros são heurísticos e podem produzir falsos positivos ou falsos
  negativos.
- `two_factor_required` é recomendação; esta API não executa 2FA.
- `approved` não autoriza liquidação e `rejected` não comprova fraude.

## Documentos de entrega

- [Matriz de negócio e resiliência](docs/PIX_SENTINEL_MATRIZ_NEGOCIO_E_RESILIENCIA.md)
- [Auditoria dos requisitos oficiais](docs/PIX_SENTINEL_AUDITORIA_REQUISITOS_OFICIAIS.md)
- [Defesa de negócio e roteiro de sete minutos](docs/PIX_SENTINEL_DEFESA_E_ROTEIRO.md)
- [Política contextual V2 candidata](docs/PIX_SENTINEL_POLITICA_V2_CANDIDATA.md)
- [Resultados V1 × V2 × ablação](docs/PIX_SENTINEL_RELATORIO_V2_CANDIDATA.md)
- [Organização do projeto](docs/como-o-projeto-e-organizado.md)
