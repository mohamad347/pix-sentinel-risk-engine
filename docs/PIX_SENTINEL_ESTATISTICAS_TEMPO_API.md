# Estatísticas de tempo da API Pix

**Data da medição:** 18/10/2026

**Política:** `mvp-risk-adaptive-v3-r9`

**Endpoint medido:** `POST /pix/transaction`

## Resumo

Foram executados 600 POSTs medidos, distribuídos em seis rodadas independentes.
Todas as requisições retornaram HTTP 200. A mediana dos resultados das três
rodadas de cada carga foi:

| Carga | p50 | p95 | p99 | Maior latência observada | Vazão |
|---|---:|---:|---:|---:|---:|
| 1 worker | 15,28 ms | 17,56 ms | 21,11 ms | 37,98 ms | 63,61 req/s |
| 8 workers | 109,03 ms | 134,41 ms | 146,45 ms | 167,06 ms | 72,88 req/s |

Neste ensaio, todas as respostas ficaram abaixo de 168 ms. Isso demonstra que o
motor responde confortavelmente dentro da exigência funcional de poucos
segundos neste ambiente e volume. Não constitui SLA nem previsão de capacidade
produtiva.

## Metodologia

- API, executor e PostgreSQL 16 rodaram em contêineres Docker na mesma rede;
- Docker Engine `29.7.2` e Docker Compose `v5.4.0` sobre Docker Desktop;
- imagem da API com um processo Uvicorn e a política R9;
- três rodadas com 1 worker e três rodadas com 8 workers;
- cada rodada teve 10 aquecimentos, excluídos das estatísticas, e 100 POSTs
  medidos;
- o PostgreSQL foi limpo antes de cada rodada;
- o banco usou o ERD normalizado em `accounts`, `devices`, `pix_transactions` e
  `pix_analyses`, com IP `INET` e histórico consultado por FKs;
- o cronômetro incluiu requisição HTTP, validação, consultas históricas,
  cálculo do score, persistência e leitura integral da resposta;
- IDs, pagadores e contas de origem eram distintos; dispositivo, IP e recebedor
  sintéticos eram compartilhados, exercitando também as consultas de rede;
- não houve TLS nem tráfego por rede externa.

Os percentis usam o método *nearest rank*. A vazão é calculada por
`requisições / tempo total da rodada`. O resumo usa a mediana das três rodadas
para p50, p95, p99 e vazão; a coluna de máximo mostra o maior valor observado
nas 300 requisições de cada carga.

## Resultados por rodada

| Workers | Rodada | p50 | p95 | p99 | Máximo | Tempo dos 100 POSTs | Vazão |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 15,24 ms | 17,56 ms | 23,61 ms | 37,98 ms | 1.572,03 ms | 63,61 req/s |
| 1 | 2 | 15,44 ms | 18,53 ms | 21,11 ms | 36,21 ms | 1.608,51 ms | 62,17 req/s |
| 1 | 3 | 15,28 ms | 16,66 ms | 17,71 ms | 18,49 ms | 1.542,87 ms | 64,81 req/s |
| 8 | 1 | 109,03 ms | 147,71 ms | 166,00 ms | 167,06 ms | 1.380,35 ms | 72,45 req/s |
| 8 | 2 | 109,97 ms | 134,41 ms | 146,45 ms | 149,20 ms | 1.372,13 ms | 72,88 req/s |
| 8 | 3 | 105,80 ms | 133,00 ms | 138,85 ms | 156,91 ms | 1.336,98 ms | 74,80 req/s |

## Interpretação

Com oito workers, a vazão mediana aumentou aproximadamente 14,6%, de 63,61 para
72,88 requisições por segundo. A latência individual cresceu porque as chamadas
concorrentes disputam um único processo da API, conexões do banco e consultas
sobre o mesmo dispositivo, IP e recebedor. Isso é esperado: concorrência pode
aumentar vazão enquanto acrescenta espera a cada requisição.

O resultado mostra que a política contextual, as consultas históricas e a
persistência não introduziram latência de segundos nesta amostra. Ele não mede:

- bases com milhões de transações;
- execução prolongada ou ponto de saturação;
- múltiplas instâncias da API;
- rede externa, TLS, proxy ou balanceador;
- latência do `GET /pix/transaction/{id}`, que não participa da decisão;
- contenção extrema de muitas requisições para o mesmo pagador;
- infraestrutura de produção com limites definidos de CPU e memória.

Por isso, os números devem ser apresentados como **benchmark local
reproduzível**, nunca como SLA.

## Como reproduzir

Suba a API R9 e o PostgreSQL isolado:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml --profile single-policy up -d --build --wait pix_test_api
```

Antes de cada rodada, limpe somente o banco descartável:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml exec -T pix_test_db psql -U pix_test -d pix_sentinel_test -c "TRUNCATE TABLE pix_analyses, pix_transactions, devices, accounts RESTART IDENTITY CASCADE;"
```

Execute a carga sequencial ou concorrente, repetindo cada uma três vezes:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml run --rm pix_test_runner python scripts/benchmark_pix_post.py --base-url http://pix_test_api:3000 --token pix-test-token --requests 100 --workers 1 --warmups 10

docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml run --rm pix_test_runner python scripts/benchmark_pix_post.py --base-url http://pix_test_api:3000 --token pix-test-token --requests 100 --workers 8 --warmups 10
```

Ao final:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml --profile single-policy down -v
```
