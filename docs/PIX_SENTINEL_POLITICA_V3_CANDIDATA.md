# Política adaptativa V3 vigente

Data: 18/10/2026. Escopo: motor antifraude Pix do hackathon. A V3 é a política
padrão; a V1 permanece selecionável explicitamente para compatibilidade.

## Resultado

A revisão vigente `mvp-risk-adaptive-v3-r4` transforma o catálogo amplo de
hipóteses em sinais executáveis sobre os
dados que o contrato realmente fornece. Ela não cria dependência de core
bancário, bureau externo, biometria, saldo ou MockServer.

O motor passa de três limiares agregados da V1 para seis famílias independentes,
com teto por família para evitar dupla contagem:

| Família | Evidências executáveis | Teto |
|---|---|---:|
| Exposição e velocidade | valor atual; rajada de 1 e 5 min; total de 60 min e 24 h; fracionamento | 60 |
| Perfil | mediana e percentil 90 de decisões anteriores aprovadas; cold start material em conta de origem jovem | 15 |
| Relacionamento | destinatário novo; repetição do par em 15 min; fan-out em 60 min | 15 |
| Acesso | device, IP e canal novos; churn local; device/IP compartilhado entre pagadores | 20 |
| Temporal | divergência entre relógios; horário incomum; velocidade geográfica derivada | 25 |
| Rede do recebedor | primeira observação global; fan-in em 60 min; conta declarada jovem | 20 |

As janelas são deslizantes `(T-W, T]` em 1, 5, 15, 60 e 1.440 minutos. O
snapshot persiste estados, contagens e grandezas derivadas, nunca documentos,
contas, IPs, device IDs ou coordenadas crus.

## Decisão e salvaguardas

- score menor que 25: `approved`;
- score entre 25 e 59: `two_factor_required`;
- acesso >= 18 combinado com relacionamento ou rede do recebedor pontuando
  recomenda 2FA mesmo quando o score fica em 23;
- rejeita com oito tentativas e R$ 30.000 para PF/indefinido ou R$ 100.000 para PJ em cinco minutos;
- rejeita velocidade geográfica superior a 1.000 km/h, ou pontos distantes em
  até 60 segundos, somente quando acesso também pontua ao menos 10;
- rejeita score 60 ou maior somente quando ao menos quatro famílias pontuam e
  acesso pontua 18 ou rede do recebedor pontua 15;
- sem uma dessas salvaguardas, score 25 ou maior recomenda 2FA;
- primeira observação local de conta de origem com menos de 30 dias e valor de
  pelo menos R$ 3.000 para PF/indefinido ou R$ 10.000 para PJ recomenda 2FA;
- ausência de histórico não vira risco automaticamente;
- perfil usa somente decisões anteriores `approved`; velocity e rede continuam
  contando tentativas, pois não são rótulos de fraude;
- sinais correlacionados são combinados dentro da família e limitados pelo teto.

O score é ordinal e explicável, não probabilidade de fraude.

Quando o resultado exige 2FA, o contrato recomenda `push_confirmation` no
nível `standard`. Somente para PF, ele eleva a recomendação para
`facial_liveness` no nível `enhanced` quando acesso pontua ao menos 18 e aparece junto de perfil >= 10,
relacionamento >= 12 ou temporal >= 15. A recomendação não executa nem declara
concluída a autenticação. Decisões aprovadas e rejeitadas omitem `challenge`.
PJ e tipo indefinido permanecem em `push_confirmation`, pois o contrato não
identifica a pessoa operadora autorizada da empresa.

## Rastreabilidade das hipóteses

O catálogo contém 127 hipóteses: 48 viáveis, 76 condicionais e 3 inviáveis na
avaliação histórica. Implementar 127 regras independentes seria incorreto porque
há variações, garantias de engenharia, hipóteses dependentes de fontes ausentes e
sinais fortemente correlacionados.

A V3 torna executáveis ou explicitamente observáveis 46 hipóteses/garantias do
catálogo em 29 sinais pontuáveis, além dos estados diagnósticos:

| Bloco | IDs cobertos | Implementação |
|---|---|---|
| Valor e multijanelas | L01, L02, L03, L05, L06, V01, V02, V03, V07, M01, M02, M03 | valor, cold start explícito, 1m/5m/60m/24h, conta jovem e horário |
| Perfil | P01, P02, P03, P04, P05, N02, N09 | suficiência, cobertura, mediana, p90, baseline aprovado-only |
| Contraparte/rede | V06, P07, C01, C02, C03, C04, C05, C06, N06, N12 | novidade local/global, par, fan-in e fan-out |
| Origem/tempo | P08, O01, O02, O03, O04, O05, O06, O07, O08, O09, G04 | device/IP/canal, compartilhamento entre pagadores, relógio e geo derivada |
| Engenharia/metadata | V08, N03, E01, MT01, MT08, MT09 | idempotência, lock, segmentos diagnósticos, snapshot privado e versionado |

Não implementados de propósito: saldo/limite/cadastro bancário, listas externas,
biometria, reputação de dispositivo, autenticação real e qualquer inferência que
exija fontes `B` ou `X`. Também não se usa status anterior como rótulo de fraude.

## Comparação executada

O conjunto A01–A10 foi criado junto da V3 e, portanto, caracteriza comportamento;
não é holdout independente nem prova de eficácia produtiva.

| Cenário | V1 | V2 R2 | V3 e score |
|---|---|---|---|
| Rotina habitual | aprovado | aprovado, 0 | aprovado, 0 |
| R$ 5 mil em cold start | 2FA | 2FA, 25 | 2FA, 25 |
| Rajada crítica objetiva | rejeitado | rejeitado, 70 | rejeitado, 70 |
| R$ 50 mil acumulados em 24 h, fora de 60 min | aprovado | aprovado, 0 | 2FA, 25 |
| Tomada de conta com velocidade impossível | aprovado | aprovado, 18 | rejeitado, 57 |
| Fan-in + conta jovem + IP compartilhado | aprovado | aprovado, 0 | 2FA, 36 |
| Desvio de perfil + novo destino/acesso | aprovado | 2FA, 33 | 2FA, 45 |
| Lote PJ legítimo na fronteira crítica | rejeitado | rejeitado, 60 | aprovado, 15 |
| Convergência crítica de quatro famílias | aprovado | 2FA, 40 | rejeitado, 65 |
| Velocidade impossível sem anomalia de acesso | aprovado | aprovado, 0 | 2FA, 25 |

Intenções sintéticas atendidas: V1 `3/10`, V2 R2 `4/10`, V3 `10/10`. O resultado
completo e os breakdowns estão em
`docs/PIX_SENTINEL_RESULTADOS_V3_CENARIOS.json`. Na R4, o cenário A08 representa
um lote PJ compatível com seu próprio perfil. Quatro operações em um minuto não
atingem o limiar PJ de seis; oito em cinco minutos geram 15 pontos, insuficientes
para fricção isolada. Isso remove a duplicidade entre 1 e 5 minutos sem neutralizar
uma microrrajada empresarial mais intensa.

Nos 30 cenários antigos B2/C2/C3, a projeção retrospectiva da V3 preserva os
resultados da R2 (`5/9`, `5/9`, `7/12`). Isso era esperado: aqueles cenários não
possuem IP, canal, fan-in ou anomalia temporal e não são reutilizados como falsa
evidência de ganho.

## Como executar

A seleção abaixo é opcional porque a V3 já é o padrão:

```powershell
$env:PIX_RISK_POLICY = "mvp-risk-adaptive-v3-r4"
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from pix_test_runner
```

## Parecer

**GO para demonstração como política vigente; NO-GO para alegar eficácia
produtiva.** A V3 melhora materialmente a profundidade exigida pelo
desafio e demonstra engenharia de features, privacidade e prevenção de dupla
contagem. Ainda faltam dados rotulados independentes e calibração produtiva dos
limiares segmentados de PF/PJ.

