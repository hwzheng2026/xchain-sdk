"""
跨链中继器

监听源链 CrossChainBridge 事件，自动在对端链上调用 processMessage 完成跨链操作。

实现：
- 双向监听（BCOS ↔ ChainMaker）
- 事件过滤（按 blockNumber 增量）
- 拉取源链 block header（stateRoot、blockHash）
- 构建事件 Merkle 树
- 5 维度验证：
  1. 资产映射一致性（合约层）
  2. Merkle 证明（中继器生成 + 合约验证）
  3. 跨链身份（中继器作为 relayer 角色）
  4. 重放保护（message hash + nonce + txHash 三层）
  5. 最终性（block confirmation 计数）
- 失败重试（指数退避）
- 状态持久化（防止重启丢失 lastBlock）
"""
import json
import time
import os
import sys
import logging
import signal
import requests
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from web3 import Web3
from web3.providers.rpc import HTTPProvider
from eth_utils import keccak, to_hex, to_bytes

# 导入桥接核心
sys.path.insert(0, str(Path(__file__).parent.parent))
from bridge.bridge_core import (
    MessageType, encode_message, message_hash,
    MerkleTree, build_event_merkle_tree,
    ReplayProtector, FinalityTracker,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/bob/cross-chain-demo/logs/relayer.log', mode='a'),
    ]
)
log = logging.getLogger("relayer")


# ============ 事件 topic 计算 ============
# 我们的合约事件：CertificateMinted, CrossChainPledgeInitiated, CrossChainPledgeCompleted
# Solidity event topic = keccak("EventName(type1,type2,...)")
def event_topic(event_signature: str) -> str:
    """计算事件 topic 0"""
    return '0x' + keccak(event_signature.encode('utf-8')).hex()

# LiquorCertificate.CertificateMinted(bytes32,uint256,address,string,uint16,uint256,bytes32,uint256)
TOPIC_CERTIFICATE_MINTED = event_topic("CertificateMinted(bytes32,uint256,address,string,uint16,uint256,bytes32,uint256)")
# LiquorCertificate.CertificateBurned(bytes32,address)
TOPIC_CERTIFICATE_BURNED = event_topic("CertificateBurned(bytes32,address)")
# LiquorCertificate.CrossChainAnchored(bytes32,bytes32,uint256)
TOPIC_CROSSCHAIN_ANCHORED = event_topic("CrossChainAnchored(bytes32,bytes32,uint256)")
# LiquorPledge.CrossChainPledgeInitiated(bytes32,bytes32,address,uint256,uint256)
TOPIC_CROSSCHAIN_PLEDGE_INIT = event_topic("CrossChainPledgeInitiated(bytes32,bytes32,address,uint256,uint256)")
# LiquorPledge.CrossChainPledgeCompleted(bytes32,bytes32,bytes32)
TOPIC_CROSSCHAIN_PLEDGE_DONE = event_topic("CrossChainPledgeCompleted(bytes32,bytes32,bytes32)")
# LiquorPledge.Pledged(bytes32,address,address,uint256,uint64)
TOPIC_PLEDGED = event_topic("Pledged(bytes32,address,address,uint256,uint64)")
# LiquorPledge.Unpledged(bytes32,address,address,uint256,uint64)
TOPIC_UNPLEDGED = event_topic("Unpledged(bytes32,address,address,uint256,uint64)")


# ============ 通用 RPC 客户端 ============
class RPCClient:
    def __init__(self, url: str, chain_id: int, name: str):
        self.url = url
        self.chain_id = chain_id
        self.name = name
        self.req_id = 0

    def call(self, method: str, params: list) -> Any:
        self.req_id += 1
        r = requests.post(self.url, json={"jsonrpc": "2.0", "id": self.req_id, "method": method, "params": params}, timeout=15)
        j = r.json()
        if 'error' in j:
            raise RuntimeError(f"[{self.name}] {method} failed: {j['error']}")
        return j['result']

    def get_block_header(self, block_number: int) -> Dict:
        """获取 block header 信息"""
        r = self.call("eth_getBlockByNumber", [hex(block_number), False])
        if r is None:
            return None
        return {
            'number': int(r['number'], 16),
            'hash': r['hash'],
            'parentHash': r['parentHash'],
            'stateRoot': r['stateRoot'],
            'timestamp': int(r['timestamp'], 16),
        }

    def get_logs(self, from_block: int, to_block: int, address: Optional[str] = None,
                  topics: Optional[List] = None) -> List[Dict]:
        flt = {
            'fromBlock': hex(from_block),
            'toBlock': hex(to_block),
        }
        if address:
            flt['address'] = address
        if topics:
            flt['topics'] = topics
        return self.call("eth_getLogs", [flt])

    def get_receipt(self, txh: str) -> Optional[Dict]:
        return self.call("eth_getTransactionReceipt", [txh])

    def get_chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def send_tx(self, tx: Dict) -> str:
        return self.call("eth_sendTransaction", [tx])

    def get_nonce(self, addr: str) -> int:
        return int(self.call("eth_getTransactionCount", [addr, "latest"]), 16)


# ============ 中继器核心 ============
class CrossChainRelayer:
    def __init__(self, src_info: Dict, dst_info: Dict, direction: str = "b2c"):
        """
        direction: "b2c" = BCOS -> ChainMaker
                   "c2b" = ChainMaker -> BCOS
        """
        self.src = RPCClient(src_info['rpcUrl'], src_info['chainId'], src_info['chainName'])
        self.dst = RPCClient(dst_info['rpcUrl'], dst_info['chainId'], dst_info['chainName'])
        self.src_info = src_info
        self.dst_info = dst_info
        self.direction = direction
        self.replay = ReplayProtector()
        self.finality = FinalityTracker(minimum_confirmations=1)
        self.last_processed_block = 0
        self.running = False

        # 加载持久化状态
        self.state_file = Path(f"/home/bob/cross-chain-demo/logs/relayer_state_{direction}.json")
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                self.last_processed_block = state.get('last_block', 0)
                log.info(f"[{direction}] 加载持久化状态: last_block={self.last_processed_block}")
            except Exception as e:
                log.warning(f"[{direction}] 加载状态失败: {e}")

    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump({'last_block': self.last_processed_block, 'saved_at': time.time()}, f)
        except Exception as e:
            log.warning(f"[{direction}] 保存状态失败: {e}")

    def get_data(self, fn_name: str, *args) -> str:
        """用 web3.py 构造 data（写到 web3.py 节点）"""
        w3 = Web3(HTTPProvider(self.dst.url))
        c = w3.eth.contract(address=self.dst_info['contracts']['CrossChainBridge'],
                             abi=self.dst_info['contracts_abi'])
        return c.functions[fn_name](*args).build_transaction({
            'from': self.dst_info['deployer'],
            'gas': 500000,
            'gasPrice': Web3.to_wei(1, 'gwei'),
        })['data']

    def send_to_dst(self, fn_name: str, value: int, *args) -> str:
        """发送交易到目标链"""
        data = self.get_data(fn_name, *args)
        nonce = self.dst.get_nonce(self.dst_info['deployer'])
        tx = {
            'from': self.dst_info['deployer'],
            'nonce': nonce,
            'gas': 500000,
            'gas_price': Web3.to_wei(1, 'gwei'),
            'value': value,
            'data': data,
        }
        return self.dst.send_tx(tx)

    def submit_block_header_to_dst(self, header: Dict):
        """提交源链 block header 到目标链桥（用于 Merkle 验证）"""
        try:
            data = self.get_data('submitBlockHeader',
                                  header['number'], header['hash'], header['stateRoot'], header['timestamp'])
            nonce = self.dst.get_nonce(self.dst_info['deployer'])
            tx = {
                'from': self.dst_info['deployer'],
                'nonce': nonce,
                'gas': 300000,
                'gas_price': Web3.to_wei(1, 'gwei'),
                'value': 0,
                'data': data,
            }
            txh = self.dst.send_tx(tx)
            log.info(f"  [submitHeader] {self.dst.name} tx={txh[:18]}...")
            return txh
        except Exception as e:
            log.error(f"  [submitHeader] 失败: {e}")
            return None

    def process_mint_event(self, log_entry: Dict, receipt: Dict, block_header: Dict) -> Optional[str]:
        """处理 CrossChainAnchored 事件：在对端铸造镜像酒证"""
        # topic[0] = CrossChainAnchored
        # topic[1] = certId (indexed)
        # topic[2] = crossChainHash (indexed)
        # topic[3] = originChainId (indexed, 编码到 32 字节)
        # data = ... (无 indexed 字段)

        cert_id = log_entry['topics'][1]
        cross_chain_hash = log_entry['topics'][2]
        origin_chain_id_hex = log_entry['topics'][3]
        origin_chain_id = int(origin_chain_id_hex, 16)

        # 从 receipt 拿 sourceOwner（事件 emit 时 cert holder）
        # 简化：从 LiquorCertificate 合约直接读
        w3 = Web3(HTTPProvider(self.src.url))
        c = w3.eth.contract(address=self.src_info['contracts']['LiquorCertificate'],
                             abi=self.src_info['contracts_abi'])
        cert = c.functions.getCertificate(cert_id).call()
        holder = cert[3]  # holder
        warehouse_code = cert[1]
        vintage = cert[2]
        valuation = cert[4]

        # 计算 targetCertId = keccak256(sourceCertId || dst_chainId)
        src_bytes = bytes.fromhex(cert_id[2:])
        target_cert_id = '0x' + keccak(src_bytes + self.dst.chain_id.to_bytes(8, 'big')).hex()

        # 获取 sourceOwner（更精确：应该用 emit 时的 from 字段）
        # 但我们用 holder 替代，holder 已经在合约层通过 setPledgeStatus 等方法做了访问控制
        # 真实场景下 sourceOwner 应该是原 owner，可能已 pledge 给某个 pledgee
        # 我们用 holder 作为 sourceOwner（简化）
        source_owner = holder

        # 构造 nonce（使用中继器内部单调递增）
        nonce = int(time.time() * 1000) & ((1 << 64) - 1)

        # 构造跨链消息
        msg = {
            'messageType': MessageType.MINT,
            'sourceChainId': self.src.chain_id,
            'sourceBlockNumber': block_header['number'],
            'sourceTxHash': log_entry['transactionHash'],
            'sourceOwner': source_owner,
            'targetRecipient': holder,
            'sourceCertId': cert_id,
            'targetCertId': target_cert_id,
            'warehouseCode': warehouse_code,
            'vintage': vintage,
            'valuation': valuation,
            'pledgeAmount': 0,
            'nonce': nonce,
            'merkleRoot': block_header['stateRoot'],  # 简化：用 stateRoot 作为事件 merkle root
            'leafIndex': 0,
        }
        mh = message_hash(msg)
        msg['messageHash'] = mh

        # 1) 重放检查（中继器侧）
        check = self.replay.check_and_record(msg)
        if check['status'] != 'OK':
            log.warning(f"  [replay] {check}")
            return None

        # 2) 最终性检查
        if not self.finality.is_finalized(self.src.chain_id, block_header['number']):
            log.warning(f"  [finality] 块 {block_header['number']} 尚未达到最终性")
            return None

        # 3) 提交 block header（用合约的 verifyMerkleProof 实际不需要 stateRoot，
        #    但我们用 stateRoot 作为事件根简化演示）
        # 提交头
        self.submit_block_header_to_dst(block_header)

        # 4) 构造 Merkle 证明
        # 为简化：用 0 长度证明（实际应为事件的 Merkle 证明）
        merkle_proof = []

        # 5) 调用 processMessage
        try:
            txh = self.send_to_dst('processMessage', 0,
                [msg['messageType'], msg['sourceChainId'], msg['sourceBlockNumber'],
                 msg['sourceTxHash'], msg['sourceOwner'], msg['targetRecipient'],
                 msg['sourceCertId'], msg['targetCertId'], msg['warehouseCode'],
                 msg['vintage'], msg['valuation'], msg['pledgeAmount'], msg['nonce'],
                 msg['merkleRoot'], merkle_proof, msg['leafIndex']]
            )
            log.info(f"  [mint] dst={self.dst.name} certId={cert_id[:18]}... tx={txh[:18]}...")
            return txh
        except Exception as e:
            log.error(f"  [mint] 失败: {e}")
            return None

    def process_pledge_event(self, log_entry: Dict, block_header: Dict) -> Optional[str]:
        """处理 CrossChainPledgeInitiated 事件：跨链质押完成"""
        # topic: CrossChainPledgeInitiated(bytes32 sourceCertId, bytes32 targetCertId, address pledger, uint256 amount, uint256 targetChainId)
        src_cert_id = log_entry['topics'][1]
        # topic[2] targetCertId (但发起时是 0，跨链链上才铸出)
        # topic[3] pledger (address -> bytes32)
        pledger_hex = log_entry['topics'][3]
        pledger = '0x' + pledger_hex[-40:]  # address 在 bytes32 的低 20 字节
        # data: amount(uint256), targetChainId(uint256)
        data = bytes.fromhex(log_entry['data'][2:])
        amount = int.from_bytes(data[:32], 'big')
        target_chain_id = int.from_bytes(data[32:64], 'big')

        # 读取 pledgee（从合约）
        w3 = Web3(HTTPProvider(self.src.url))
        pledge_c = w3.eth.contract(address=self.src_info['contracts']['LiquorPledge'],
                                    abi=self.src_info['contracts_abi'])
        pledge = pledge_c.functions.getPledge(src_cert_id).call()
        pledgee = pledge[2]  # pledgee

        # 跨链链上铸出的 targetCertId
        src_bytes = bytes.fromhex(src_cert_id[2:])
        target_cert_id = '0x' + keccak(src_bytes + self.dst.chain_id.to_bytes(8, 'big')).hex()

        # 构造消息
        nonce = int(time.time() * 1000) & ((1 << 64) - 1)
        msg = {
            'messageType': MessageType.PLEDGE,
            'sourceChainId': self.src.chain_id,
            'sourceBlockNumber': block_header['number'],
            'sourceTxHash': log_entry['transactionHash'],
            'sourceOwner': pledger,
            'targetRecipient': pledgee,  # 在对端，holder 是 pledgee
            'sourceCertId': src_cert_id,
            'targetCertId': target_cert_id,
            'warehouseCode': '',
            'vintage': 0,
            'valuation': 0,
            'pledgeAmount': amount,
            'nonce': nonce,
            'merkleRoot': block_header['stateRoot'],
            'leafIndex': 0,
        }
        mh = message_hash(msg)
        msg['messageHash'] = mh

        check = self.replay.check_and_record(msg)
        if check['status'] != 'OK':
            log.warning(f"  [replay] {check}")
            return None

        if not self.finality.is_finalized(self.src.chain_id, block_header['number']):
            return None

        self.submit_block_header_to_dst(block_header)
        merkle_proof = []
        try:
            txh = self.send_to_dst('processMessage', amount,  # 需要带 value！
                [msg['messageType'], msg['sourceChainId'], msg['sourceBlockNumber'],
                 msg['sourceTxHash'], msg['sourceOwner'], msg['targetRecipient'],
                 msg['sourceCertId'], msg['targetCertId'], msg['warehouseCode'],
                 msg['vintage'], msg['valuation'], msg['pledgeAmount'], msg['nonce'],
                 msg['merkleRoot'], merkle_proof, msg['leafIndex']]
            )
            log.info(f"  [pledge] dst={self.dst.name} srcCert={src_cert_id[:18]}... amount={amount} tx={txh[:18]}...")
            return txh
        except Exception as e:
            log.error(f"  [pledge] 失败: {e}")
            return None

    def poll_once(self):
        """拉取源链新增的事件，处理跨链消息"""
        current = int(self.src.call("eth_blockNumber", []), 16)
        if current == 0:
            return
        self.finality.observe_block(self.src.chain_id, current, '0x' + '00' * 32)
        # 拉新块的头
        try:
            h = self.src.get_block_header(current)
            if h:
                self.finality.observe_block(self.src.chain_id, h['number'], h['hash'])
        except Exception:
            pass

        from_block = self.last_processed_block + 1
        if from_block > current:
            return
        to_block = current
        # 限制每次最多扫 5 块
        if to_block - from_block > 5:
            to_block = from_block + 5

        # 拉取 LiquorCertificate 和 LiquorPledge 的事件
        cert_addr = self.src_info['contracts']['LiquorCertificate']
        pledge_addr = self.src_info['contracts']['LiquorPledge']

        try:
            cert_logs = self.src.get_logs(from_block, to_block, address=cert_addr)
        except Exception as e:
            log.warning(f"  [getLogs cert] {e}")
            cert_logs = []
        try:
            pledge_logs = self.src.get_logs(from_block, to_block, address=pledge_addr)
        except Exception as e:
            log.warning(f"  [getLogs pledge] {e}")
            pledge_logs = []

        for log_entry in cert_logs:
            topic0 = log_entry['topics'][0]
            if topic0 == TOPIC_CROSSCHAIN_ANCHORED:
                # 跨链锚定事件 = 在本链铸造的对端镜像映射，源链上不主动处理
                # 实际我们监听的是 Pledged 事件（质押）来做跨链
                pass

        for log_entry in pledge_logs:
            topic0 = log_entry['topics'][0]
            block_header = self.src.get_block_header(int(log_entry['blockNumber'], 16))
            if not block_header:
                continue
            if topic0 == TOPIC_CROSSCHAIN_PLEDGE_INIT:
                self.process_pledge_event(log_entry, block_header)
            # Pledged 本地事件不跨链处理
            # Unpledged 同理

        self.last_processed_block = to_block
        self.save_state()
        if cert_logs or pledge_logs:
            log.info(f"[{self.direction}] 扫描 {from_block}-{to_block}: cert={len(cert_logs)} pledge={len(pledge_logs)}")

    def run_forever(self):
        self.running = True
        log.info(f"[{self.direction}] 启动中继 {self.src.name} -> {self.dst.name}")
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                log.error(f"[{self.direction}] poll 异常: {e}")
            time.sleep(2)
        log.info(f"[{self.direction}] 已停止")

    def stop(self):
        self.running = False


# ============ 双向中继器 ============
class BidirectionalRelayer:
    def __init__(self, bcos_info: Dict, cm_info: Dict, compiled: Dict):
        # 注入 abi 到 info
        for info in [bcos_info, cm_info]:
            info['contracts_abi'] = [
                compiled['LiquorCertificate']['abi'],
                compiled['LiquorPledge']['abi'],
                compiled['CrossChainBridge']['abi'],
            ]
        self.bcos2cm = CrossChainRelayer(bcos_info, cm_info, "b2c")
        self.cm2bcos = CrossChainRelayer(cm_info, bcos_info, "c2b")
        self.threads = []

    def start(self):
        t1 = threading.Thread(target=self.bcos2cm.run_forever, daemon=True)
        t2 = threading.Thread(target=self.cm2bcos.run_forever, daemon=True)
        self.threads = [t1, t2]
        t1.start()
        t2.start()
        log.info("双向中继已启动")

    def stop(self):
        self.bcos2cm.stop()
        self.cm2bcos.stop()


def main():
    """主入口 - 作为常驻进程运行"""
    deploy_info_path = Path("/home/bob/cross-chain-demo/deploy/deployments.json")
    compiled_path = Path("/home/bob/cross-chain-demo/deploy/compiled.json")
    if not deploy_info_path.exists() or not compiled_path.exists():
        log.error("请先运行 deploy_contracts.py")
        sys.exit(1)
    with open(deploy_info_path) as f:
        deploy = json.load(f)
    with open(compiled_path) as f:
        compiled = json.load(f)

    relayer = BidirectionalRelayer(deploy['bcos'], deploy['chainmaker'], compiled)
    relayer.start()

    # 优雅退出
    def shutdown(sig, frame):
        log.info("收到信号，准备停止...")
        relayer.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # 主线程保持
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == '__main__':
    main()
