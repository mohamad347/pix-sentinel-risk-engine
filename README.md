# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

O PIX SENTINEL é um motor antifraude transacional para solicitações Pix ainda
não executadas. Ele recebe os dados oficiais do Pix, consulta o histórico local
de tentativas da conta pagadora e devolve um parecer explicável:
`approved`, `two_factor_required` ou `rejected`.

O parecer não consulta saldo, não movimenta dinheiro, não comprova fraude e não
executa autenticação adicional.

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

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| Valor elevado | Recomendar verificação adicional quando o valor atual atingir o limite | P1 `VALUE_2FA_CENTS = 500_000` centavos (R$ 5.000) | Uma transferência pontual elevada justifica fricção proporcional, mas nunca rejeição isolada |
| Frequência elevada | Recomendar verificação adicional quando a janela de 5 minutos atingir o limite | P2 `COUNT_2FA_5M = 4` tentativas, incluindo a atual | Ritmo elevado pode indicar automação ou uso anômalo da conta |
| Montante acumulado | Recomendar verificação adicional quando o total tentado em 60 minutos atingir o limite | P3 `TOTAL_2FA_60M_CENTS = 1_500_000` centavos (R$ 15.000) | Várias tentativas podem acumular exposição mesmo quando cada valor isolado é moderado |
| Rajada crítica | Primeiro componente da rejeição: contagem de 5 minutos no limite crítico | P4 `COUNT_REJECT_5M = 8` tentativas | Uma rajada muito intensa eleva o risco, mas não basta sozinha para rejeitar |
| Rajada e volume críticos | Rejeitar somente quando P4 e P5 ocorrerem simultaneamente | P5 `TOTAL_REJECT_5M_CENTS = 3_000_000` centavos (R$ 30.000), junto de P4 | Combina ritmo e volume tentado em uma heurística crítica; ainda não prova fraude |

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

O `metadata` público contém somente versão e motivo. O snapshot interno
`pix-history-v1` registra contagens, somas em centavos, instante de admissão e
códigos dos sinais acionados, sem documento, conta, IP ou geolocalização.

## Como rodar

Uma instalação nova é inicializada por `database/database.sql`:

```bash
docker compose up --build
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

Validação local executada em Windows com Docker Desktop, API e PostgreSQL nos
contêineres isolados: 138 testes aprovados, nenhuma falha ou skip. Uma medição
E2E sequencial com 100 payloads sintéticos e pagadores distintos obteve:

| Rota | p50 | p95 | p99 |
|---|---:|---:|---:|
| POST `/pix/transaction` | 75,18 ms | 111,65 ms | 148,35 ms |
| GET `/pix/transaction/{id}` | 9,95 ms | 15,31 ms | 19,52 ms |

Esses números descrevem apenas o ambiente local de teste; não são benchmark de
produção nem estimativa de capacidade sob concorrência.

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
- Não há reputação externa, perfil bancário cadastrado ou detecção enriquecida
  de contraparte.
- Device, IP e geolocalização são declarados pelo payload e não autenticam o
  titular.
- Os parâmetros são heurísticos e podem produzir falsos positivos ou falsos
  negativos.
- `two_factor_required` é recomendação; esta API não executa 2FA.
- `approved` não autoriza liquidação e `rejected` não comprova fraude.
