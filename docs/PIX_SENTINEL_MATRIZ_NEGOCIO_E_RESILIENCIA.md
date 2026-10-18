# PIX SENTINEL — matriz de negócio e resiliência

**Política avaliada:** `mvp-risk-experimental-v1`

**Execução registrada:** 18/10/2026, API e PostgreSQL 16 isolados em Docker Desktop

**Natureza dos dados:** todos os documentos, contas, valores e rótulos são sintéticos.

Os termos “legítimo”, “suspeito” e “crítico” abaixo descrevem a intenção do
cenário. Eles não são verdade de fraude nem demonstram precisão, recall ou taxa
de falso positivo. Quando PF/PJ aparece, a classificação é inferida apenas do
formato sintético de 11 ou 14 dígitos do documento. O motor não consulta cadastro
e a política v1 não possui thresholds distintos para PF e PJ.

## Matriz executada

Todos os dez casos foram executados por `POST /pix/transaction` e conferidos no
snapshot persistido. `T5/T60` são contagem e montante nas janelas de 5 e 60
minutos, incluindo a tentativa atual.

| ID | Caso e hipótese sintética | Evidências observadas | Esperado v1 | Produzido | Explicação, risco e evolução candidata |
|---|---|---|---|---|---|
| B01 | **PF, primeiro Pix baixo.** Cold start legítimo de R$ 100. | T5 `1/R$100`; T60 `1/R$100`; contraparte `insufficient_history`. | `approved` | `approved`, `RISK_WITHIN_EXPERIMENTAL_POLICY`, sem sinais. | Cold start é neutro. Pode aprovar risco invisível ao histórico local; evolução depende de fontes confiáveis ainda inexistentes. |
| B02 | **PF, primeiro Pix elevado.** Transferência legítima pontual de R$ 5.000. | T5/T60 `1/R$5.000`; histórico insuficiente. | `two_factor_required` | `two_factor_required`, sinal `HIGH_CURRENT_VALUE`. | Valor mede exposição, não fraude. Há fricção legítima deliberada; a API recomenda 2FA, mas não autentica. |
| B03 | **PF, Pix rotineiro.** Segunda tentativa de R$ 100 para destinatário já usado. | T5/T60 `2/R$200`; contraparte `observed`, 1 anterior. | `approved` | `approved`, sem sinais. | Frequência e valor permanecem abaixo dos limites. Familiaridade é informativa e não altera a v1. |
| B04 | **PF, quatro Pix pequenos.** Quatro pagamentos de R$ 100 em 5 minutos. | T5/T60 `4/R$400`; contraparte observada. | `two_factor_required` | `two_factor_required`, sinal `HIGH_ATTEMPT_COUNT_5M`. | Velocity detecta concentração temporal, mas pode impor 2FA a pagamentos legítimos em sequência. |
| B05 | **PF, montante acumulado.** Quatro tentativas de R$ 3.750 distribuídas em 60 minutos. | T5 `1/R$3.750`; T60 `4/R$15.000`; contraparte observada. | `two_factor_required` | `two_factor_required`, somente `HIGH_ATTEMPT_TOTAL_60M`. | Isola o sinal agregado. O relógio histórico foi preparado de modo controlado no banco descartável; não foi espera real de 60 minutos. |
| B06 | **PF, rajada crítica.** Oito Pix de R$ 3.750, total R$ 30.000 em 5 minutos. | T5/T60 `8/R$30.000`; quatro sinais de frequência/montante. | `rejected` | `rejected`, `SUSPICIOUS_BURST_AND_VOLUME`. | A rejeição exige frequência **e** montante críticos. Os sinais são correlacionados e não são provas independentes; uma rajada legítima pode ser rejeitada. |
| B07 | **PJ, pagamentos recorrentes.** Quatro pagamentos legítimos de R$ 100 ao mesmo fornecedor. | Documento sintético de 14 dígitos; T5 `4/R$400`; contraparte observada, 3 anteriores. | `two_factor_required` | `two_factor_required`, `HIGH_ATTEMPT_COUNT_5M`. | Falso positivo sintético de fricção: a v1 não segmenta PJ e não reduz risco por familiaridade. Uma política futura precisaria de justificativa e calibração. |
| B08 | **PF, contraparte observada.** Segundo Pix baixo ao mesmo destinatário. | T5/T60 `2/R$200`; contraparte `observed`, 1 anterior. | `approved` | `approved`, sem sinais. | O contexto ajuda a explicar, mas não certifica reputação e não muda a decisão. |
| B09 | **PF, contraparte não observada.** Segundo Pix baixo, agora para outro destinatário. | T5/T60 `2/R$200`; contraparte `not_observed`, 0 anteriores. | `approved` | `approved`, sem sinais. | Uma contraparte não observada não provoca fricção isoladamente. Pode haver falso negativo, mas “nova no histórico local” não significa fraudulenta ou nova na vida bancária. |
| B10 | **Replay de decisão persistida.** Reenvio idêntico de um Pix de R$ 5.000. | Primeira resposta 2FA; replay com mesmo fingerprint. | Mesma decisão | Dois HTTP `200`, resposta e evidência idênticas; 1 transação e 1 análise. | O replay não reconsulta histórico nem reavalia política. Após resultado de rede desconhecido, o cliente reconcilia pelo mesmo `external_id`. |

## Cadeia de decisão

### Valor atual

- **Problema:** uma tentativa isolada pode concentrar exposição financeira.
- **Hipótese:** valor atual elevado justifica verificação adicional.
- **Evidência:** `amount_cents` validado como inteiro no payload oficial.
- **Regra:** valor `>= 500_000` centavos aciona `HIGH_CURRENT_VALUE`.
- **Resultado:** `two_factor_required`, nunca rejeição apenas por valor.
- **Limitação:** renda, finalidade e perfil financeiro são desconhecidos; uma
  transferência legítima elevada recebe fricção.

### Velocity

- **Problema:** automação ou abuso pode concentrar tentativas no tempo.
- **Hipótese:** muitas tentativas distintas em 5 minutos justificam contenção.
- **Evidência:** contagem local em `(T-5m, T]`, por conta pagadora e sem replays.
- **Regra:** 4 tentativas acionam 2FA; 8 compõem a condição crítica.
- **Resultado:** a frequência sozinha produz no máximo 2FA.
- **Limitação:** PF e PJ compartilham o mesmo limite; pagamentos legítimos em lote
  podem gerar fricção.

### Montante acumulado

- **Problema:** valores moderados podem acumular exposição relevante.
- **Hipótese:** a soma em centavos complementa a observação do valor atual.
- **Evidência:** `SUM(amount_cents)` de tentativas distintas em 5 e 60 minutos.
- **Regra:** R$ 15.000 em 60 minutos aciona 2FA; R$ 30.000 em 5 minutos compõe a
  condição crítica.
- **Resultado:** sinal agregado explicável e sem ponto flutuante.
- **Limitação:** contagem e soma derivam das mesmas tentativas e são
  correlacionadas; não devem ser apresentadas como evidências independentes.

### Rejeição combinada

- **Problema:** rejeitar por um único sinal aumenta falsos positivos.
- **Hipótese:** coexistência de rajada extrema e volume crítico representa risco
  maior que qualquer condição isolada.
- **Evidência:** pelo menos 8 tentativas **e** R$ 30.000 em `(T-5m, T]`.
- **Regra:** as duas condições precisam ocorrer simultaneamente.
- **Resultado:** `rejected / SUSPICIOUS_BURST_AND_VOLUME` tem precedência.
- **Limitação:** uma operação legítima em lote também satisfaz a combinação. A
  correlação reduz a força da interpretação e os thresholds não foram calibrados.

### Cold start e contraparte

- **Problema:** ausência de histórico local não descreve a vida bancária do
  cliente.
- **Hipótese:** cold start e destinatário não observado não devem ser tratados
  isoladamente como fraude.
- **Evidência:** `prior_attempt_count` e `pix-counterparty-v1`.
- **Regra:** ambos são informativos; não alteram a decisão v1.
- **Resultado:** B01 e B09 permanecem aprovados quando nenhum sinal P1–P5 dispara.
- **Limitação:** o motor pode deixar de elevar risco real que não apareça em valor
  ou velocity. Familiaridade local também não prova legitimidade.

## Resiliência comprovada

| Condição | Prova executada | Resultado |
|---|---|---|
| Falha na consulta histórica | Repository de teste lança erro depois do `flush`. | Rollback; política não executada; nenhuma análise fabricada. |
| Falha na política | Política de teste lança `RiskPolicyUnavailable`. | HTTP técnico/rollback; nunca aprovação automática. |
| Falha antes do commit | Inserção da análise e commit são forçados a falhar em testes unitários. | Rollback e nenhuma resposta de sucesso. |
| PostgreSQL indisponível | Sessão HTTP ASGI lança `OperationalError` no primeiro acesso. | `500 QIT000500`, sessão revertida/fechada e resposta sem PII. |
| Espera excessiva no lock | Outra conexão segura o mesmo advisory lock no PostgreSQL real. | `lock_timeout=1000ms`, `500 QIT000500`, zero linhas parciais. |
| Mesmo ID, payload idêntico | Duas requisições sincronizadas e teste forçado de colisão UNIQUE. | Uma transação/análise; ambas recuperam a mesma decisão. |
| Mesmo ID, payload divergente | Duas requisições simultâneas com mesmo ID. | Um `200`, um `409`; vencedor permanece imutável. |
| IDs distintos, mesmo pagador | Duas conexões sincronizadas pelo teste. | Histórico serializado: contagens `1` e `2`. |
| IDs distintos, pagadores distintos | Quatro requisições sincronizadas. | Quatro `200`; cada snapshot inicia com histórico zero. |
| GET inexistente | Consulta HTTP de ID ausente. | `404 QIT002002`. |
| Política antiga | Análise bootstrap persistida e reenviada sob a política ativa v1. | Versão, chave, status e metadata originais preservados. |

Um timeout do cliente após envio não prova que o commit falhou. O servidor pode
ter confirmado a transação e perdido apenas a entrega da resposta. Por isso a
reconciliação correta é reenviar o mesmo payload e `external_id`: se o commit
existir, a API devolve a decisão persistida; se o ID estiver associado a outro
conteúdo oficial, devolve `409`. O sistema não promete rollback depois que o
commit pode ter sido confirmado.

## Medição concorrente local

Método: 5 aquecimentos, depois 80 POSTs com IDs e pagadores distintos, 8 workers,
API e PostgreSQL isolados em Docker Desktop no mesmo computador.

| Requisições | Workers | HTTP 200 | Transações | Análises | p50 | p95 | p99 | Máximo |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 80 | 8 | 80 | 80 | 80 | 285,24 ms | 343,95 ms | 353,43 ms | 362,93 ms |

Tempo total: 2.796,82 ms; vazão observada local: 28,6 req/s. Esta medição é
pequena, local e limitada de propósito. Não representa SLA, capacidade produtiva
ou comportamento sob carga sustentada.

## Limites para a entrega

- Os parâmetros P1–P5 são heurísticos e não foram calibrados em fraude real.
- A política não segmenta PF/PJ, renda, porte ou finalidade do pagamento.
- Histórico e contraparte existem somente no universo observado pelo PIX
  SENTINEL.
- Uma tentativa rejeitada continua sendo tentativa, não fraude confirmada.
- `two_factor_required` é recomendação; esta API não autentica o usuário.
- `approved` não liquida o Pix e `rejected` não prova fraude.
- O limite de espera do lock protege a API contra bloqueio prolongado, mas pode
  produzir erro técnico transitório sob contenção; o cliente deve repetir com o
  mesmo `external_id`.
