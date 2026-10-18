# PIX SENTINEL — relatório da política contextual V2 candidata

**Data:** 18/10/2026  
**V1 padrão:** `mvp-risk-experimental-v1`  
**V2 opt-in:** `mvp-risk-contextual-v2-candidate`

## Resultado executivo

A V2 calcula score ordinal de 0–100 e usa contexto real do histórico local. O
score alterou decisões: alto valor isolado no limite, quatro pagamentos
recorrentes e rajada crítica com contexto conhecido deixaram de receber a mesma
ação automática da V1; salto de perfil com dispositivo/contraparte novos passou
a recomendar 2FA mesmo abaixo de condições isoladas da V1.

O resultado não justifica ativação padrão. Em C2, V2 coincidiu com 5 de 9
intenções sintéticas, V1 com 4 de 9 e a ablação com 2 de 9. A amostra tem baixa
potência, os rótulos não são fraude real e a V2 rebaixa uma rajada crítica com
contexto familiar de rejeição para 2FA. O parecer é **NO-GO para ativação como
padrão** e **GO para testes isolados/shadow e nova avaliação independente**.

## Implementação

- `src/controllers/pix_contextual_risk_policy.py`: configuração validada,
  score, caps, bandas, salvaguarda e breakdown interno;
- `src/controllers/pix_policy_selector.py`: V1 default e V2 somente com seleção
  explícita em `APP_ENV=test`;
- `src/utils/pix_context.py`: estados de dispositivo, perfil, diversidade,
  geo derivada, disponibilidade e PF/PJ diagnósticos;
- `src/repositories/pix_transaction_repository.py`: agregados PostgreSQL;
- `src/controllers/pix_transaction_controller.py`: solicita contexto somente
  quando a política ativa declara essa necessidade;
- `scripts/evaluate_contextual_v2.py`: B2/C2 determinístico e ablação;
- `scripts/benchmark_pix_post.py`: medição local reproduzível;
- `tests/unit/test_pix_contextual_risk_policy.py`, `test_pix_context.py`,
  `test_pix_policy_selector.py` e `test_pix_policy_experiments.py`;
- `tests/integration/pix/test_pix_contextual_v2_flow.py`.

Nenhuma tabela, coluna, índice, endpoint ou dependência foi adicionada.

## Evidências e SQL

A identidade do pagador continua documento + agência + conta. A tentativa atual
é persistida e recebe `flush()` antes das consultas. O agregado usa o mesmo
filtro por pagador e `received_at <= T`:

```sql
COUNT(id) FILTER (WHERE id <> :current_id)
COUNT(id) FILTER (WHERE id <> :current_id AND source_device_id = :current_device)
PERCENTILE_DISC(0.5) WITHIN GROUP (
  ORDER BY CASE WHEN id <> :current_id THEN amount_cents END
)
COUNT(DISTINCT (payee_participant, payee_branch,
                payee_account_number, payee_document))
  FILTER (WHERE received_at > T - W)
```

`MIN/MAX(received_at)` anteriores determinam a abrangência do perfil. Geo usa
uma segunda consulta limitada a uma linha: última tentativa anterior do mesmo
pagador cujo JSONB possua geolocalização objeto. O snapshot guarda somente
distância e tempo derivados, sem coordenadas. O índice existente
`ix_pix_transactions_payer_received_at` atende o recorte por pagador/tempo.

Na V1, `include_contextual=False`; sua consulta e snapshot continuam no formato
legado. Na V2, os novos agregados são habilitados explicitamente.

## Resultado A — invariantes

| Invariante | Evidência executada | Resultado |
|---|---|---|
| Score inteiro e saturado em 0–100 | testes puros de cold start, perfil e combinação crítica | Passou |
| Correlação/cap | rajada com valor, contagem e soma simultâneos | família exposição limitada a 45 |
| Missingness e cold start | geo ausente, perfil/device insuficientes | zero ponto |
| Monotonicidade | aumento de contagem/montante com demais sinais fixos | score não diminuiu |
| Geo/PF/PJ neutros | geo observada e quatro classificações PF/PJ | zero ponto |
| Config/evidência inválida | bandas inválidas e estado geo desconhecido | erro técnico |
| PII | snapshots e respostas inspecionados | sem documento, conta, device, IP ou coordenadas |
| Idempotência e 409 | POST/GET/replay/conflito V2 no PostgreSQL | mesma resposta; conflito seguro |
| Concorrência | mesmo ID igual/diferente e IDs distintos do mesmo pagador | 6 testes V2/PostgreSQL aprovados |
| V1 preservada | suíte completa com API V1 | aprovada; V2 exclusiva ignorada |

## Resultado B2 e C2

Seed fixa: `20261018`. B2 foi declarado como desenvolvimento. Os parâmetros
foram congelados antes da leitura de C2 e não foram ajustados depois.

| Caso | Classe | Intenção sintética | V1 | V2 | Ablação | Score V2 | Leitura |
|---|---|---|---|---|---|---:|---|
| B2-L1 | B2 | legítimo | approved | approved | approved | 0 | cold start neutro |
| B2-L2 | B2 | legítimo | 2FA | approved | approved | 15 | reduz fricção de alto valor isolado |
| B2-L3 | B2 | legítimo | 2FA | approved | approved | 20 | reduz fricção de PJ recorrente |
| B2-D1 | B2 | dúvida | 2FA | 2FA | approved | 40 | perfil acrescenta poder decisório |
| B2-D2 | B2 | dúvida | 2FA | 2FA | approved | 43 | device + dispersão são contextuais |
| B2-D3 | B2 | dúvida | 2FA | approved | approved | 20 | possível subfricção da V2 |
| B2-C1 | B2 | crítico | rejected | rejected | 2FA | 100 | contexto é necessário para banda crítica |
| B2-C2 | B2 | crítico | 2FA | 2FA | approved | 55 | não rejeita sem salvaguarda |
| B2-C3 | B2 | crítico | 2FA | 2FA | 2FA | 60 | contagem sem volume crítico não rejeita |
| C2-L1 | C2 | legítimo | approved | approved | approved | 0 | rotina preservada |
| C2-L2 | C2 | legítimo | 2FA | approved | approved | 20 | menor fricção sintética em PJ |
| C2-L3 | C2 | legítimo | 2FA | 2FA | 2FA | 30 | alto volume único ainda recebe 2FA |
| C2-D1 | C2 | dúvida | 2FA | 2FA | approved | 43 | contexto muda a ablação |
| C2-D2 | C2 | dúvida | approved | 2FA | approved | 30 | V2 encontra salto contextual abaixo da V1 |
| C2-D3 | C2 | dúvida | approved | approved | approved | 0 | geo declarada fica diagnóstica; possível falso negativo |
| C2-C1 | C2 | crítico | rejected | 2FA | 2FA | 45 | regressão deliberadamente conservadora com contexto familiar |
| C2-C2 | C2 | crítico | rejected | rejected | 2FA | 90 | rejeição contextual reproduzível |
| C2-C3 | C2 | crítico | 2FA | 2FA | approved | 60 | score alto sem rajada não rejeita |

| Conjunto/política | Coincidências | Fricção em legítimos | Rejeições de legítimos | Críticos aprovados |
|---|---:|---:|---:|---:|
| B2 / V1 | 5/9 | 2/3 | 0 | 0 |
| B2 / V2 | 6/9 | 0/3 | 0 | 0 |
| B2 / ablação | 3/9 | 0/3 | 0 | 1/3 |
| C2 / V1 | 4/9 | 2/3 | 0 | 0 |
| C2 / V2 | 5/9 | 1/3 | 0 | 0 |
| C2 / ablação | 2/9 | 1/3 | 0 | 1/3 |

O JSON completo, incluindo entradas, evidências, breakdowns e reasons, está em
`docs/PIX_SENTINEL_RESULTADOS_V2_A_B2_C2.json`.

## B01–B10 sob a candidata

Os resultados V1 abaixo são os executados na Fatia 6. V2 foi aplicada offline
às mesmas evidências, sem criar novas tentativas.

| Caso | V1 persistida | V2 candidata | Score/observação |
|---|---|---|---|
| B01 primeiro Pix baixo | approved | approved | 0 |
| B02 primeiro Pix de R$ 5.000 | 2FA | approved | 15; redução de fricção, com risco de subfricção |
| B03 Pix rotineiro | approved | approved | 0 |
| B04 quatro Pix pequenos | 2FA | approved | 20 com mesmo device/destino |
| B05 R$ 15 mil em 60m | 2FA | approved | 20; divergência a revisar |
| B06 oito Pix/R$ 30 mil | rejected | 2FA | 45 sem contexto adverso adicional |
| B07 PJ recorrente | 2FA | approved | 20; reduz falso positivo sintético |
| B08 contraparte observada | approved | approved | 0 |
| B09 contraparte não observada com pouco histórico | approved | approved | 0; novidade isolada neutra |
| B10 replay de V1 | 2FA original | 2FA original | não reavaliado pela V2 |

## PostgreSQL e demonstração V2

Três fluxos próprios passaram no PostgreSQL 16 isolado:

1. cold start de R$ 5.000: `approved`, score interno 15;
2. perfil com cinco anteriores, mediana R$ 500, novo device e contraparte:
   `two_factor_required`, score 55;
3. oitava tentativa diversa, R$ 30 mil/5m e device novo: `rejected`, score 75.

Para o terceiro caso, GET e replay devolveram a mesma chave
`43ec709c-c182-47d2-8ccb-b44fefac8dce`, status e metadata. Conteúdo divergente
retornou `409 QIT002004`. O score apareceu somente no `evidence_snapshot`.

## Performance local

Método: API e PostgreSQL em Docker Desktop, banco isolado, três aquecimentos,
40 POSTs com IDs/pagadores distintos. O ensaio concorrente usou 8 workers.

| Política | Workers | p50 | p95 | p99 | Máximo |
|---|---:|---:|---:|---:|---:|
| V1 padrão final | 1 | 41,39 ms | 125,74 ms | 210,01 ms | 210,01 ms |
| V2 candidata | 1 | 40,67 ms | 55,10 ms | 71,01 ms | 71,01 ms |
| V1 padrão final | 8 | 315,89 ms | 532,01 ms | 558,94 ms | 558,94 ms |
| V2 candidata | 8 | 266,00 ms | 434,12 ms | 449,27 ms | 449,27 ms |

As diferenças são ruído local e efeito de ordem/cache; não provam que V2 seja
mais rápida. V2 amplia o agregado SQL e pode executar uma consulta adicional de
geo limitada a uma linha. Os valores permanecem abaixo de poucos segundos neste
ensaio, sem constituir SLA ou capacidade produtiva.

## Testes executados

| Comando/seleção | Ambiente | Resultado |
|---|---|---:|
| `python -m pytest -q tests/unit` | local, sem banco | 91 passed, 1 warning, 2,55 s |
| testes V2 + três cenários concorrentes | API V2 + PostgreSQL 16 isolado | 6 passed, 1 warning, 4,32 s |
| `python -m pytest -q` com opt-in e API V1 | PostgreSQL 16 isolado | 119 passed, 3 skipped, 1 warning, 8,46 s |
| `python -m pytest -q` sem opt-in | comando padrão | 91 passed, 31 skipped, 1 warning, 4,13 s |
| `python scripts/evaluate_contextual_v2.py` | offline, seed fixa | B2 e C2 regenerados |

Os três skips na suíte integrada V1 são os fluxos que exigem a API explicitamente
selecionada em V2. Os 31 skips do comando padrão são os 28 testes PostgreSQL da
V1 e os três da V2. O único warning é a API depreciada `declarative_base()` do
SQLAlchemy. Ao final, o container de teste foi devolvido à V1 padrão.

## Limitações e parecer

- 18 cenários não medem precisão, recall ou fraude real;
- C2 foi consultado uma vez, mas continua sintético e pequeno;
- mediana de tentativas pode sofrer envenenamento e não representa gasto
  liquidado;
- device, geo, IP e canal são declarados e não autenticam ninguém;
- perfil exige cinco anteriores e abrangência de 24h; abaixo disso é neutro;
- V2 reduz fricção em casos legítimos sintéticos, mas também reduz a ação em B05
  e B06/C2-C1;
- geo distante permanece aprovada, expondo uma limitação consciente da fonte;
- a ablação piorou, mostrando que contexto não é decorativo, mas o ganho C2 de
  uma coincidência é insuficiente para ativação.

**Parecer:** NO-GO para substituir V1. Manter V1 padrão; usar V2 somente em
testes isolados ou shadow offline. Uma decisão futura precisa de conjunto
independente maior, revisão explícita das divergências B05/B06 e análise do custo
operacional em dados de volume representativo.
