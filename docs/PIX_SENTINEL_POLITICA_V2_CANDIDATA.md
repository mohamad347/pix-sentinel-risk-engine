# PIX SENTINEL — política contextual V2 candidata

**Versão:** `mvp-risk-contextual-v2-candidate`

**Estado:** candidata selecionável somente por configuração explícita em ambiente
de teste. `mvp-risk-experimental-v1` continua sendo a política padrão.

## Problema e fontes

A V1 usa valor atual, contagem e montante em janelas. A V2 testa se contexto
local reduz fricção ou identifica combinações que a V1 não distingue. Ela usa
somente `D` (payload oficial), `H` (tentativas persistidas pelo PIX SENTINEL) e
`S` (`clock_timestamp()` do PostgreSQL). Não usa core bancário, saldo, DICT,
reputação externa, autenticação real ou dados de liquidação.

Os pesos e bandas abaixo são hipóteses novas desta candidata. Não reutilizam
`grid-10`, `e4-exp-0.1`, pesos ou thresholds do laboratório da Etapa 4. O
holdout antigo já foi consultado e não será usado para reajustar esta política.

## Features, score e correlação

| Família | Fórmula candidata | Fonte e precondição | Pontos / teto | Correlação e falso positivo | Teste principal |
|---|---|---|---:|---|---|
| Exposição e velocity | Máximo entre faixa de valor atual, faixa de contagem 5m e faixa de montante; bônus somente na combinação crítica | D/H/S; tentativa atual incluída uma vez | valor: 15/25; contagem: 20/30; montante: 20/30; combinação crítica +15; **teto 45** | Valor, contagem e soma não são somados como provas independentes. Lotes legítimos continuam possíveis | monotonicidade, teto e rajada |
| Desvio de perfil | Compara valor atual com mediana das tentativas anteriores | H exclui atual; mínimo 5 anteriores e abrangência mínima de 24h | razão ≥3: 15; razão ≥8: 25; **teto 25** | Histórico contém tentativas, não liquidações; mediana reduz influência de extremos, mas não elimina envenenamento | amostra/span insuficientes e salto de perfil |
| Contraparte e diversidade | Máximo entre contraparte não observada e diversidade de destinatários em 60m | H exclui atual para familiaridade; novidade só após 3 anteriores | nova: 5; ≥4 destinos: 8; ≥8 destinos: 15; **teto 15** | Destino observado não é confiável e PJ pode pagar muitos fornecedores | cold start neutro e diversidade |
| Dispositivo | `device_id` ainda não observado na conta, após histórico mínimo | H exclui atual; mínimo 3 anteriores | novo: 10; novo junto de velocity ≥4: 15; **teto 15** | Não significa dispositivo não cadastrado ou roubado | mudança isolada e mudança + velocity |
| Geolocalização | Distância e tempo desde a última geo válida, sem coordenadas no snapshot | D/H/S; geo opcional e declarada | **0** | GPS pode estar ausente ou impreciso; não autentica titular | `missing`, `insufficient_history`, `observed`, `invalid` |
| Canal/IP | Somente disponibilidade, sem armazenar valores no snapshot | D | **0** | NAT, VPN e canal declarado são fontes fracas | neutralidade e ausência de PII |
| PF/PJ | Classificação diagnóstica quando o documento permite | D; 11 dígitos → PF, 14 caracteres alfanuméricos → PJ | **0** | Não há perfil econômico nem threshold segmentado | PF/PJ não altera score |

Cada família é inteira e limitada; o score final é a soma dos tetos, saturada em
`[0, 100]`. Ausência ou insuficiência de evidência vale zero. Consulta quebrada
é erro técnico e nunca se converte em ausência.

## Bandas e salvaguarda de rejeição

- `0–29`: `approved` / `CONTEXTUAL_RISK_LOW`;
- `30–69`: `two_factor_required` / `CONTEXTUAL_VERIFICATION_RECOMMENDED`;
- `70–100`: ainda exige a salvaguarda crítica;
- `rejected` / `CONTEXTUAL_CRITICAL_COMBINATION` somente quando score ≥70 **e**
  coexistem pelo menos 8 tentativas e R$ 30.000 tentados em 5 minutos;
- score ≥70 sem essa combinação continua em `two_factor_required`.

O score governa aprovação versus 2FA. A condição crítica adicional existe para
evitar rejeição por uma soma de sinais fracos. Contagem e montante da salvaguarda
são correlacionados e não são apresentados como provas independentes.

## Exemplo calculado à mão

Considere a oitava tentativa em 5 minutos, total de R$ 30.000, valor atual de
R$ 3.750, dispositivo ainda não observado após sete tentativas, oito destinos
distintos e valor oito vezes acima da mediana histórica:

1. exposição/velocity: `max(0, 30, 30) + 15 = 45`;
2. perfil: razão ≥8 → `25`;
3. contraparte/diversidade: oito destinos → `15`;
4. dispositivo: novo com velocity → `15`;
5. geo/canal/PF-PJ: `0`.

`45 + 25 + 15 + 15 = 100`. Como o score está na banda crítica e a salvaguarda
de rajada+montante também ocorre, o resultado é `rejected`. Se a mesma rajada
usar dispositivo e contraparte observados e não desviar do perfil, o score é
45 e o resultado é 2FA, expondo deliberadamente o trade-off frente à V1.

## Evidência e privacidade

O snapshot interno mantém `pix-history-v1` e acrescenta contexto versionado. O
breakdown contém apenas estados, contagens, somas, razão em basis points,
distância derivada e pontos; não contém documento, conta, device ID, IP,
coordenadas ou payload. A resposta pública continua exatamente `key`, `status`
e `metadata`. O metadata expõe versão e categoria geral do motivo, sem score,
pesos, thresholds ou evidências detalhadas.

## Seleção e compatibilidade

`PIX_RISK_POLICY` ausente seleciona V1. O valor
`mvp-risk-contextual-v2-candidate` é aceito somente com `APP_ENV=test`. GET e
replay devolvem decisões antigas sem reavaliação. Comparações offline aplicam V1,
V2 e uma ablação sem contexto ao mesmo snapshot, sem novas linhas no banco.

## Experimentos

- **A:** invariantes, limites, missingness, caps, privacidade, falhas, replay e
  concorrência;
- **B2:** casos de desenvolvimento com seed fixa; parâmetros candidatos só
  podem ser ajustados aqui;
- **C2:** casos reservados e independentes, lidos uma vez, sem reajuste;
- **baselines:** V1, V2 e V2 sem perfil/contraparte/dispositivo.

Os rótulos `legitimo`, `duvida` e `critico` são intenção sintética. Não medem
fraude real, precisão produtiva ou vantagem estatística.
