# Auditoria e correção da política contextual V2 R2

Data: 18/10/2026. Escopo: motor antifraude Pix; sem core bancário, liquidação ou autenticação real.

## Diagnóstico e decisão

| Exigência | Símbolo auditado | V2 original | R2 |
|---|---|---|---|
| V1 padrão | `build_risk_policy` | preservada | preservada |
| Valor de R$ 5.000 | `value_signal`, `two_factor_score` | 15 pontos; `approved` | 25 pontos; `two_factor_required` |
| Rajada crítica familiar | `exposure_cap`, `reject_score` | 45 pontos; podia virar 2FA | 60 pontos; `rejected` |
| Perfil | `profile_critical` | até 25 | até 15 |
| Baseline do perfil | `get_payer_history` | todas as tentativas | apenas pareceres anteriores `approved` |
| Velocity | janelas `(T-W, T]` | todas as tentativas | todas as tentativas |
| Versionamento | `policy_version` | `mvp-risk-contextual-v2-candidate` | `mvp-risk-contextual-v2-candidate-r2` |

A V2 original não foi alterada. A R2 é selecionável somente quando `APP_ENV=test` e
`PIX_RISK_POLICY=mvp-risk-contextual-v2-candidate-r2`. O padrão continua
`mvp-risk-experimental-v1`.

A baseline R2 exclui `two_factor_required` e `rejected` porque esses pareceres não
devem normalizar automaticamente valores de risco. Isso reduz um vetor evidente de
envenenamento, mas não transforma `approved` em prova de legitimidade ou liquidação.
Essa limitação permanece explícita.

## Score R2

- exposição/velocity: 0, 25 ou 60; usa um único máximo por família;
- perfil: 0, 10 ou 15;
- contraparte/diversidade: até 15;
- dispositivo: até 15;
- geolocalização: zero, somente diagnóstico;
- `<25`: `approved`; `25–59`: `two_factor_required`; `>=60` rejeita somente com a
  salvaguarda explícita da rajada crítica conjunta.

O score é ordinal e determinístico, não probabilidade de fraude. A rejeição continua
exigindo simultaneamente 8 ou mais tentativas e R$ 30.000 ou mais em 5 minutos.

## Cenários auditados

| Caso | Contexto | V1 | V2 original | R2 e breakdown |
|---|---|---|---|---|
| C03 | cold start pequeno | approved | approved, 0 | approved, 0 |
| C04 | cold start R$ 5.000 | 2FA | approved, 15 | 2FA, 25 exposição |
| C05b | rajada crítica familiar | rejected | 2FA, 45 | rejected, 60 exposição |
| C01 | R$ 4.000, perfil maduro, device e destino novos | approved | 2FA | 2FA, perfil 15 + contraparte 5 + device 10 = 30 |
| Perfil contaminado | 5 aprovadas baixas + 6 não aprovadas altas | — | mediana misturada | mediana R$ 100; amostra 5; 11 tentativas ainda contam em velocity |
| PJ legítima em lote crítico | rajada familiar de R$ 32.000 | rejected | 2FA, 45 | rejected, 60; falso positivo sintético conhecido |

## Experimento B2/C2/C3

C2 é diagnóstico histórico e não foi reutilizado como holdout. A R2 foi congelada
antes de definir e executar C3. C3 possui 12 casos sintéticos novos e não foi usado
para recalibrar a política.

| Conjunto/política | Intenções atendidas | Legítimos com fricção | Legítimos rejeitados | Críticos A/2FA/R | Rebaixamentos de rejeição V1 |
|---|---:|---:|---:|---:|---:|
| B2 V1 | 5/9 | 2/3 | 0 | 0/2/1 | — |
| B2 V2 original | 6/9 | 0/3 | 0 | 0/2/1 | 0 |
| B2 R2 | 5/9 | 2/3 | 0 | 0/2/1 | 0 |
| C2 V1 | 4/9 | 2/3 | 0 | 0/1/2 | — |
| C2 V2 original | 5/9 | 1/3 | 0 | 0/2/1 | 1 |
| C2 R2 | 5/9 | 2/3 | 0 | 0/1/2 | 0 |
| C3 V1 | 6/12 | 2/4 | 1 | 0/2/2 | — |
| C3 V2 original | 6/12 | 1/4 | 0 | 0/3/1 | 1 |
| C3 R2 | 7/12 | 2/4 | 1 | 0/2/2 | 0 |

Resultados completos e breakdown por caso:
`docs/PIX_SENTINEL_RESULTADOS_V2_R2_A_B2_C2_C3.json`.

## Evidência executada

- unitários: `101 passed`;
- suíte completa com API V1 e PostgreSQL isolado: `129 passed, 6 skipped`;
- integração R2 no PostgreSQL isolado: `3 passed`;
- replay, conflito 409, concorrência, lock por pagador e rollback selecionados na R2:
  `6 passed`;
- integração da V2 original: `3 passed`.

O PostgreSQL de teste usa `pix_sentinel_test`, credenciais próprias e `tmpfs`. Nesta
máquina foram usadas as portas host 15432/3301 porque 55432 pertence a uma faixa
reservada do Windows. Nenhum banco de desenvolvimento foi apagado ou reinicializado.

## Parecer

**NO-GO para promoção da R2 a padrão.** A correção satisfaz C04, C05b, versionamento,
replay e separação entre velocity e baseline, mas os dados são sintéticos, a amostra
C3 é pequena e a rejeição de uma rajada PJ legítima evidencia falso positivo relevante.

**GO apenas para manter a R2 como candidata opt-in e submetê-la à aprovação de João e
Moha.** A V1 deve permanecer padrão até essa decisão e até validação com dados rotulados
ou critérios de negócio adicionais defensáveis.
