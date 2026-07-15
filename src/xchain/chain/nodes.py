"""
EVM 节点服务 - 模拟 FISCO BCOS / ChainMaker 联盟链节点

底层使用 py-evm（真实 EVM 实现），对外提供：
- JSON-RPC 端点
- 链 ID 区分两条链
- 块生成（PoA 风格，立即出块）
- event log 查询
- 状态根计算
- receipt 生成

使用：
  python3 evm_node_service.py --chain-id 1001 --name bcos --port 8545 --data-dir /tmp/bcos-data
  python3 evm_node_service.py --chain-id 2001 --name chainmaker --port 8546 --data-dir /tmp/cm-data
"""
import argparse
import json
import os
import sys
import time
import threading
import http.server
import socketserver
from typing import Dict, Any, List, Optional

# py-evm imports
from eth_account import Account
from eth_utils import (
    to_bytes, to_hex, to_int, to_checksum_address, encode_hex, keccak,
    to_wei, from_wei, decode_hex, is_address
)

# eth-tester
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionNotFound, BlockNotFound
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider
from web3.exceptions import TransactionNotFound as W3TxNotFound


class EVMRPCServer:
    """基于 eth-tester + py-evm 的 JSON-RPC 服务"""

    def __init__(self, chain_id: int, name: str, port: int, data_dir: str):
        self.chain_id = chain_id
        self.name = name
        self.port = port
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # 创建 eth-tester (后端是 py-evm)
        backend = PyEVMBackend()
        self.tester = EthereumTester(backend=backend)

        # 创建 web3 客户端
        self.w3 = Web3(EthereumTesterProvider(self.tester))

        # 配置 chain ID
        # eth-tester 默认 chainId 取决于 backend，需要 hack
        # 通过发送一条带 chainId 的交易来"激活"（仅作记录用途）
        self._accounts = self.tester.get_accounts()
        self._genesis_time = int(time.time())
        self._block_counter = 0
        self._chain_name = name

        # 预存 100 ETH 给前 5 个账户，方便演示
        for i, acct in enumerate(self._accounts[:5]):
            self.tester.send_transaction({
                'from': self._accounts[0],
                'to': acct,
                'value': to_wei(100, 'ether'),
                'gas': 100000,
                'gas_price': to_wei(1, 'gwei'),
            })
        # 清空 nonce 让后续交易从 0 开始（eth-tester 的 nonce 由后端管理）

        # block header 历史（用于 finality 检查）
        self._block_headers: Dict[int, Dict[str, Any]] = {}

        # 锁定
        self._lock = threading.RLock()

        # 立即生成一个空块，确保初始状态
        self._maybe_mine_empty()

    @property
    def accounts(self) -> List[str]:
        return [to_checksum_address(a) for a in self._accounts]

    def _maybe_mine_empty(self):
        """每 1 秒出一个空块（PoA 风格）"""
        def miner():
            while True:
                try:
                    time.sleep(1.0)
                    # 检查是否有 pending tx
                    # eth-tester 的 mine_blocks 自动清空 mempool
                    with self._lock:
                        # 拿当前最新块号
                        try:
                            current = self.w3.eth.block_number
                        except Exception:
                            current = 0
                        new_block = current + 1
                        self.tester.mine_blocks(1)
                        # 记录 block header
                        try:
                            blk = self.w3.eth.get_block(new_block, full_transactions=False)
                            self._block_headers[new_block] = {
                                'number': blk.number,
                                'hash': blk.hash.hex() if isinstance(blk.hash, bytes) else blk.hash,
                                'parentHash': blk.parentHash.hex() if isinstance(blk.parentHash, bytes) else blk.parentHash,
                                'stateRoot': blk.stateRoot.hex() if isinstance(blk.stateRoot, bytes) else blk.stateRoot,
                                'timestamp': blk.timestamp,
                                'gasUsed': blk.gasUsed,
                                'finalized': True,  # PoA 立即最终
                            }
                        except Exception as e:
                            pass
                except Exception as e:
                    sys.stderr.write(f"[{self.name}] miner error: {e}\n")
        t = threading.Thread(target=miner, daemon=True)
        t.start()

    # ============ JSON-RPC 方法 ============
    def rpc(self, method: str, params: List[Any]) -> Any:
        """统一的 JSON-RPC 调度"""
        with self._lock:
            try:
                if method == 'eth_chainId':
                    return hex(self.chain_id)
                elif method == 'eth_blockNumber':
                    return hex(self.w3.eth.block_number)
                elif method == 'eth_getBalance':
                    addr = params[0]
                    block = params[1] if len(params) > 1 else 'latest'
                    bal = self.w3.eth.get_balance(self.w3.to_checksum_address(addr), block)
                    return hex(bal)
                elif method == 'eth_getTransactionCount':
                    addr = params[0]
                    block = params[1] if len(params) > 1 else 'latest'
                    n = self.w3.eth.get_transaction_count(self.w3.to_checksum_address(addr), block)
                    return hex(n)
                elif method == 'eth_getCode':
                    addr = params[0]
                    block = params[1] if len(params) > 1 else 'latest'
                    code = self.w3.eth.get_code(self.w3.to_checksum_address(addr), block)
                    return '0x' + code.hex() if code else '0x'
                elif method == 'eth_call':
                    call = params[0]
                    block = params[1] if len(params) > 1 else 'latest'
                    result = self.w3.eth.call(call, block)
                    return '0x' + result.hex() if result else '0x'
                elif method == 'eth_estimateGas':
                    return hex(self.w3.eth.estimate_gas(params[0]))
                elif method == 'eth_gasPrice':
                    return hex(to_wei(1, 'gwei'))
                elif method == 'eth_sendTransaction':
                    tx = params[0]
                    return self._send_tx(tx)
                elif method == 'eth_sendRawTransaction':
                    raw = params[0]
                    return self._send_raw(raw)
                elif method == 'eth_getTransactionReceipt':
                    h = params[0]
                    return self._get_receipt(h)
                elif method == 'eth_getTransactionByHash':
                    h = params[0]
                    return self._get_tx(h)
                elif method == 'eth_getBlockByHash':
                    h = params[0]
                    full = params[1] if len(params) > 1 else False
                    return self._get_block_by_hash(h, full)
                elif method == 'eth_getBlockByNumber':
                    n = params[0]
                    full = params[1] if len(params) > 1 else False
                    return self._get_block_by_number(n, full)
                elif method == 'eth_getLogs':
                    return self._get_logs(params[0] if params else {})
                elif method == 'eth_accounts':
                    return self.accounts
                elif method == 'net_version':
                    return str(self.chain_id)
                elif method == 'web3_clientVersion':
                    return f"{self._chain_name}/py-evm-0.12"
                elif method == 'eth_subscribe' or method == 'eth_unsubscribe':
                    return "0x0"
                # 自定义扩展方法
                elif method == 'bcos_getBlockHeader':
                    return self._bcos_get_header(params[0])
                elif method == 'chainmaker_getBlockHeader':
                    return self._bcos_get_header(params[0])
                else:
                    raise ValueError(f"Method not implemented: {method}")
            except TransactionNotFound:
                return None
            except BlockNotFound:
                return None
            except W3TxNotFound:
                return None
            except Exception as e:
                raise RuntimeError(f"RPC {method} error: {e}")

    def _send_tx(self, tx: Dict) -> str:
        # eth-tester 需要 from 是私钥账户
        from_addr = tx.get('from')
        if not from_addr:
            raise ValueError("Missing 'from'")
        from_addr_cs = self.w3.to_checksum_address(from_addr)
        from_addr_lc = from_addr_cs.lower()
        # 找该地址对应的私钥
        priv = None
        try:
            priv = self.tester.backend._key_lookup.get(from_addr_lc)
        except Exception:
            priv = None
        if priv is None:
            # 尝试 send_transaction
            txh = self.tester.send_transaction(tx)
        else:
            tx_full = {
                **tx,
                'nonce': self.w3.eth.get_transaction_count(from_addr_cs),
                'chainId': self.chain_id,
                'gasPrice': to_wei(1, 'gwei'),
            }
            tx_signed = Account.sign_transaction(tx_full, priv)
            txh = self.w3.eth.send_raw_transaction(tx_signed.rawTransaction)
        # 立即 mine 一次以包含这笔交易
        try:
            self.tester.mine_blocks(1)
            with self._lock:
                bn = self.w3.eth.block_number
                try:
                    blk = self.w3.eth.get_block(bn, full_transactions=False)
                    self._block_headers[bn] = {
                        'number': blk.number,
                        'hash': blk.hash.hex() if isinstance(blk.hash, bytes) else blk.hash,
                        'parentHash': blk.parentHash.hex() if isinstance(blk.parentHash, bytes) else blk.parentHash,
                        'stateRoot': blk.stateRoot.hex() if isinstance(blk.stateRoot, bytes) else blk.stateRoot,
                        'timestamp': blk.timestamp,
                        'gasUsed': blk.gasUsed,
                        'finalized': True,
                    }
                except Exception:
                    pass
        except Exception:
            pass
        return '0x' + txh.hex() if not isinstance(txh, str) else txh

    def _send_raw(self, raw: str) -> str:
        txh = self.w3.eth.send_raw_transaction(bytes.fromhex(raw[2:] if raw.startswith('0x') else raw))
        return txh.hex()

    def _get_receipt(self, h: str):
        try:
            r = self.w3.eth.get_transaction_receipt(h)
        except Exception:
            return None
        if r is None:
            return None
        def hx(x):
            if x is None:
                return None
            if isinstance(x, bytes):
                return '0x' + x.hex()
            if hasattr(x, 'hex'):
                return '0x' + x.hex()
            return str(x)
        return {
            'transactionHash': hx(r.transactionHash),
            'blockHash': hx(r.blockHash),
            'blockNumber': hex(r.blockNumber) if r.blockNumber is not None else None,
            'from': r['from'] if isinstance(r, dict) and 'from' in r else (r.get('from', '')),
            'to': r.to if hasattr(r, 'to') and r.to else None,
            'cumulativeGasUsed': hex(r.cumulativeGasUsed) if r.cumulativeGasUsed is not None else '0x0',
            'gasUsed': hex(r.gasUsed) if r.gasUsed is not None else '0x0',
            'contractAddress': hx(r.contractAddress) if r.contractAddress else None,
            'logs': [{
                'address': hx(l.address),
                'topics': [hx(t) for t in l.topics],
                'data': hx(l.data),
                'blockNumber': hex(l.blockNumber) if l.blockNumber is not None else None,
                'transactionHash': hx(l.transactionHash),
                'logIndex': hex(l.logIndex) if l.logIndex is not None else '0x0',
                'transactionIndex': hex(l.transactionIndex) if l.transactionIndex is not None else '0x0',
            } for l in r.logs],
            'logsBloom': hx(r.logsBloom) if hasattr(r, 'logsBloom') and r.logsBloom else '0x' + '00'*256,
            'status': hex(r.status) if hasattr(r, 'status') and r.status is not None else '0x1',
        }

    def _get_tx(self, h: str):
        try:
            t = self.w3.eth.get_transaction(h)
        except Exception:
            return None
        if t is None:
            return None
        def hx(x):
            if x is None:
                return None
            if isinstance(x, bytes):
                return '0x' + x.hex()
            if hasattr(x, 'hex'):
                return '0x' + x.hex()
            return str(x)
        return {
            'hash': hx(t.hash),
            'nonce': hex(t.nonce) if t.nonce is not None else '0x0',
            'blockHash': hx(t.blockHash) if t.blockHash else None,
            'blockNumber': hex(t.blockNumber) if t.blockNumber else None,
            'transactionIndex': hex(t.transactionIndex) if t.transactionIndex is not None else None,
            'from': t['from'] if isinstance(t, dict) else getattr(t, 'from_', None),
            'to': t.to if hasattr(t, 'to') and t.to else None,
            'value': hex(t.value) if t.value is not None else '0x0',
            'gas': hex(t.gas) if t.gas is not None else '0x0',
            'gasPrice': hex(t.gasPrice) if t.gasPrice is not None else '0x0',
            'input': hx(t.input) if t.input else '0x',
            'v': hex(t.v) if t.v is not None else '0x0',
            'r': hex(t.r) if t.r is not None else '0x0',
            's': hex(t.s) if t.s is not None else '0x0',
        }

    def _get_block_by_hash(self, h: str, full: bool):
        try:
            b = self.w3.eth.get_block(h, full_transactions=full)
        except Exception:
            return None
        if b is None:
            return None
        return self._block_to_dict(b, full)

    def _get_block_by_number(self, n, full: bool):
        try:
            if isinstance(n, str):
                if n == 'latest':
                    bn = self.w3.eth.block_number
                elif n == 'earliest':
                    bn = 0
                elif n == 'pending':
                    bn = self.w3.eth.block_number
                else:
                    bn = int(n, 16) if n.startswith('0x') else int(n)
            else:
                bn = int(n)
            b = self.w3.eth.get_block(bn, full_transactions=full)
        except Exception:
            return None
        if b is None:
            return None
        return self._block_to_dict(b, full)

    def _block_to_dict(self, b, full: bool):
        def hx(x):
            if x is None:
                return None
            if isinstance(x, bytes):
                return '0x' + x.hex()
            return x
        d = {
            'number': hex(b.number),
            'hash': hx(b.hash),
            'parentHash': hx(b.parentHash),
            'nonce': hx(b.nonce),
            'sha3Uncles': hx(b.sha3Uncles) if hasattr(b, 'sha3Uncles') else hx(getattr(b, 'uncles_hash', None)),
            'logsBloom': hx(b.logsBloom) if hasattr(b, 'logsBloom') else '0x' + '00'*256,
            'transactionsRoot': hx(b.transactionsRoot),
            'stateRoot': hx(b.stateRoot),
            'receiptsRoot': hx(b.receiptsRoot) if hasattr(b, 'receiptsRoot') else None,
            'miner': b.miner if hasattr(b, 'miner') else b.beneficiary if hasattr(b, 'beneficiary') else None,
            'difficulty': hex(b.difficulty) if hasattr(b, 'difficulty') else '0x0',
            'totalDifficulty': hex(b.totalDifficulty) if hasattr(b, 'totalDifficulty') and b.totalDifficulty is not None else '0x0',
            'extraData': hx(b.extraData) if hasattr(b, 'extraData') else '0x',
            'size': hex(b.size) if hasattr(b, 'size') else '0x0',
            'gasLimit': hex(b.gasLimit),
            'gasUsed': hex(b.gasUsed),
            'timestamp': hex(b.timestamp),
        }
        if full:
            d['transactions'] = [
                {
                    'hash': hx(t.hash),
                    'nonce': hex(t.nonce),
                    'from': t['from'] if isinstance(t, dict) else getattr(t, 'from_', None),
                    'to': t.to,
                    'value': hex(t.value),
                    'gas': hex(t.gas),
                    'gasPrice': hex(t.gasPrice),
                    'input': hx(t.input) if isinstance(t.input, bytes) else t.input,
                } for t in b.transactions
            ]
        else:
            d['transactions'] = [hx(t) for t in b.transactions]
        return d

    def _get_logs(self, flt: Dict):
        """简化的 get_logs：扫描所有 block 的 receipts"""
        logs = []
        from_block = flt.get('fromBlock', 'latest')
        to_block = flt.get('toBlock', 'latest')
        address = flt.get('address')
        topics = flt.get('topics', [])
        # 解析 block 范围
        latest = self.w3.eth.block_number
        if from_block == 'latest' or from_block is None:
            fb = latest
        elif from_block == 'earliest':
            fb = 0
        elif from_block == 'pending':
            fb = latest
        else:
            fb = int(from_block, 16) if from_block.startswith('0x') else int(from_block)

        if to_block == 'latest' or to_block is None:
            tb = latest
        elif to_block == 'earliest':
            tb = 0
        elif to_block == 'pending':
            tb = latest
        else:
            tb = int(to_block, 16) if to_block.startswith('0x') else int(to_block)

        fb = max(0, fb)
        tb = min(latest, tb)

        # 扫描每个块的 receipt
        for bn in range(fb, tb + 1):
            try:
                b = self.w3.eth.get_block(bn, full_transactions=True)
            except Exception:
                continue
            for tx in b.transactions:
                try:
                    r = self.w3.eth.get_transaction_receipt(tx.hash)
                except Exception:
                    continue
                if r is None:
                    continue
                for log in r.logs:
                    la = '0x' + log.address.hex() if isinstance(log.address, bytes) else log.address
                    if address and la.lower() != address.lower():
                        continue
                    lt = ['0x' + t.hex() if isinstance(t, bytes) else t for t in log.topics]
                    if topics:
                        match = True
                        for i, t_filter in enumerate(topics):
                            if t_filter is None:
                                continue
                            if isinstance(t_filter, list):
                                if i >= len(lt) or lt[i] not in t_filter:
                                    match = False
                                    break
                            else:
                                if i >= len(lt) or lt[i].lower() != t_filter.lower():
                                    match = False
                                    break
                        if not match:
                            continue
                    logs.append({
                        'address': la,
                        'topics': lt,
                        'data': '0x' + log.data.hex() if isinstance(log.data, bytes) else log.data,
                        'blockNumber': hex(log.blockNumber),
                        'transactionHash': '0x' + log.transactionHash.hex() if isinstance(log.transactionHash, bytes) else log.transactionHash,
                        'logIndex': hex(log.logIndex),
                        'transactionIndex': hex(log.transactionIndex),
                    })
        return logs

    def _bcos_get_header(self, block_number) -> Dict:
        bn = int(block_number, 16) if isinstance(block_number, str) and block_number.startswith('0x') else int(block_number)
        with self._lock:
            h = self._block_headers.get(bn)
        if h:
            return {
                'number': hex(h['number']),
                'hash': '0x' + h['hash'] if not h['hash'].startswith('0x') else h['hash'],
                'stateRoot': '0x' + h['stateRoot'] if not h['stateRoot'].startswith('0x') else h['stateRoot'],
                'parentHash': '0x' + h['parentHash'] if not h['parentHash'].startswith('0x') else h['parentHash'],
                'timestamp': hex(h['timestamp']),
                'finalized': h['finalized'],
            }
        # 兜底：从 web3 拉
        try:
            b = self.w3.eth.get_block(bn)
            return {
                'number': hex(b.number),
                'hash': '0x' + b.hash.hex(),
                'stateRoot': '0x' + b.stateRoot.hex(),
                'parentHash': '0x' + b.parentHash.hex(),
                'timestamp': hex(b.timestamp),
                'finalized': True,
            }
        except Exception:
            return None


# ============ JSON-RPC HTTP 服务器 ============
class RPCHandler(http.server.BaseHTTPRequestHandler):
    rpc_server: EVMRPCServer = None  # 类级引用

    def log_message(self, format, *args):
        # 静默
        pass

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            req = json.loads(body)
            method = req.get('method')
            params = req.get('params', [])
            req_id = req.get('id', 0)
            try:
                result = self.rpc_server.rpc(method, params)
                response = {'jsonrpc': '2.0', 'id': req_id, 'result': result}
            except Exception as e:
                response = {
                    'jsonrpc': '2.0', 'id': req_id,
                    'error': {'code': -32000, 'message': str(e), 'data': {'method': method}}
                }
            resp = json.dumps(response).encode('utf-8')
        except Exception as e:
            resp = json.dumps({'jsonrpc': '2.0', 'id': 0, 'error': {'code': -32700, 'message': str(e)}}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chain-id', type=int, required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--port', type=int, required=True)
    ap.add_argument('--data-dir', default='/tmp/evm-data')
    args = ap.parse_args()

    print(f"[{args.name}] 启动 EVM 节点 (chainId={args.chain_id}, port={args.port})")
    server_obj = EVMRPCServer(args.chain_id, args.name, args.port, args.data_dir)
    RPCHandler.rpc_server = server_obj

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", args.port), RPCHandler) as httpd:
        print(f"[{args.name}] 监听 0.0.0.0:{args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"[{args.name}] 退出")


if __name__ == '__main__':
    main()
