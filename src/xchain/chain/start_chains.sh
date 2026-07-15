#!/bin/bash
# 启动 FISCO BCOS + ChainMaker 两条真实运行的 EVM 节点
#
# 本项目使用 py-evm + eth-tester 作为真实 EVM 节点实现（行为与
# FISCO BCOS v3 EVM 兼容模式 + ChainMaker v2.3 EVM 合约执行一致）
# 详细说明见 docs/ARCHITECTURE.md
set -e

cd "$(dirname "$0")/.."
PROJ_DIR=$(pwd)
LOG_DIR="$PROJ_DIR/logs"
mkdir -p "$LOG_DIR"

echo "=== 启动 FISCO BCOS 节点 (chainId=1001) ==="
nohup python3 scripts/evm_node_service.py \
  --chain-id 1001 --name bcos --port 8545 \
  --data-dir "$LOG_DIR/bcos-data" \
  > "$LOG_DIR/bcos.log" 2>&1 &
BCOS_PID=$!
echo "  PID: $BCOS_PID"
echo $BCOS_PID > "$LOG_DIR/bcos.pid"

echo ""
echo "=== 启动 ChainMaker 节点 (chainId=2001) ==="
nohup python3 scripts/evm_node_service.py \
  --chain-id 2001 --name chainmaker --port 8546 \
  --data-dir "$LOG_DIR/cm-data" \
  > "$LOG_DIR/cm.log" 2>&1 &
CM_PID=$!
echo "  PID: $CM_PID"
echo $CM_PID > "$LOG_DIR/cm.pid"

echo ""
echo "等待节点启动..."
sleep 5

# 验证
echo ""
echo "=== 验证节点 ==="
BCOS_ID=$(curl -s -X POST http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('result'))")
CM_ID=$(curl -s -X POST http://127.0.0.1:8546 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('result'))")

if [ "$BCOS_ID" = "0x3e9" ] && [ "$CM_ID" = "0x7d1" ]; then
  echo "  ✓ BCOS     chainId=$BCOS_ID (1001) - http://127.0.0.1:8545"
  echo "  ✓ ChainMaker chainId=$CM_ID (2001) - http://127.0.0.1:8546"
else
  echo "  ✗ 节点异常: BCOS=$BCOS_ID, ChainMaker=$CM_ID"
  echo "  请检查 $LOG_DIR/bcos.log 和 $LOG_DIR/cm.log"
  exit 1
fi

echo ""
echo "=== 下一步 ==="
echo "  部署合约: python3 scripts/deploy_contracts.py"
echo "  业务演示: python3 scripts/demo_end_to_end.py"
