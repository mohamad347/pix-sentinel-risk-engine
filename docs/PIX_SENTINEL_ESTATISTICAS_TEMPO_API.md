# Estatísticas de tempo da API Pix

**Data da medição:** 18/10/2026  
**Política:** `mvp-risk-adaptive-v3-r5`  
**Endpoint medido:** `POST /pix/transaction`

## Resumo

Foram executados 600 POSTs medidos, distribuídos em seis rodadas independentes.
Todas as requisições retornaram HTTP 200. A mediana dos resultados das três
rodadas de cada carga foi:

| Carga | p50 | p95 | p99 | Maior latência observada | Vazão |
|---|---:|---:|---:|---:|---:|
| 1 worker | 14,69 ms | 17,39 ms | 20,24 ms | 45,20 ms | 66,21 req/s |
| 8 workers | 101,50 ms | 137,96 ms | 144,34 ms | 171,07 ms | 77,14 req/s |

Neste ensaio, todas as respostas ficaram abaixo de 200 ms. Isso demonstra que o
motor responde confortavelmente dentro da exigência funcional de poucos
segundos neste ambiente e volume. Não constitui SLA nem previsão de capacidade
produtiva.

## Metodologia

- API, executor e PostgreSQL 16 rodaram em contêineres Docker na mesma rede;
- Docker Engine `29.7.2` e Docker Compose `v5.4.0` sobre Docker Desktop;
- imagem da API com um processo Uvicorn e a política R5;
- três rodadas com 1 worker e três rodadas com 8 workers;
- cada rodada teve 10 aquecimentos, excluídos das estatísticas, e 100 POSTs
  medidos;
- o PostgreSQL foi limpo antes de cada rodada;
- o cronômetro incluiu requisição HTTP, validação, consultas históricas,
  cálculo do score, persistência e leitura integral da resposta;
- IDs, pagadores e contas de origem eram distintos; dispositivo, IP e recebedor
  sintéticos eram compartilhados, exercitando também as consultas de rede;
- não houve TLS nem tráfego por rede externa.

Os percentis usam o método *nearest rank*. A vazão é calculada por
`requisições / tempo total da rodada`.

## Resultados por rodada

| Workers | Rodada | p50 | p95 | p99 | Máximo | Tempo dos 100 POSTs | Vazão |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 14,69 ms | 16,04 ms | 21,08 ms | 35,64 ms | 1.495,50 ms | 66,87 req/s |
| 1 | 2 | 14,85 ms | 17,39 ms | 19,37 ms | 45,20 ms | 1.541,70 ms | 64,86 req/s |
| 1 | 3 | 14,58 ms | 18,01 ms | 20,24 ms | 39,11 ms | 1.510,27 ms | 66,21 req/s |
| 8 | 1 | 99,57 ms | 124,53 ms | 131,72 ms | 137,77 ms | 1.273,12 ms | 78,55 req/s |
| 8 | 2 | 101,50 ms | 137,96 ms | 169,96 ms | 171,07 ms | 1.305,11 ms | 76,62 req/s |
| 8 | 3 | 101,96 ms | 139,18 ms | 144,34 ms | 154,98 ms | 1.296,32 ms | 77,14 req/s |

## Interpretação

Com oito workers, a vazão mediana aumentou aproximadamente 16,5%, de 66,21 para
77,14 requisições por segundo. A latência individual cresceu porque as chamadas
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

Suba a API R5 e o PostgreSQL isolado:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml --profile single-policy up -d --build --wait pix_test_api
```

Antes de cada rodada, limpe somente o banco descartável:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml exec -T pix_test_db psql -U pix_test -d pix_sentinel_test -c "TRUNCATE TABLE pix_analyses, pix_transactions RESTART IDENTITY CASCADE;"
```

Execute a carga sequencial ou concorrente, repetindo cada uma três vezes:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml run --rm pix_test_runner python scripts/benchmark_pix_post.py --base-url http://pix_test_api:3000 --token pix-test-token --requests 100 --workers 1 --warmups 10

docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml run --rm pix_test_runner python scripts/benchmark_pix_post.py --base-url http://pix_test_api:3000 --token pix-test-token --requests 100 --workers 8 --warmups 10
```

Ao final:

```powershell
docker compose -p pix-sentinel-benchmark -f docker-compose.test.yml down -v
```
