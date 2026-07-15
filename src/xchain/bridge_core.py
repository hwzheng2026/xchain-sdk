"""
跨链桥核心逻辑 - 链无关模块

提供：
- 跨链消息编解码
- Merkle 证明生成
- 重放保护
- 最终性确认

这些函数同时被中继器（off-chain）和合约（on-chain 验证）使用。
"""
import hashlib
import json
import time
from typing import List, Dict, Any, Optional
from eth_utils import keccak, to_hex, to_bytes, to_int


# ============ 消息类型 ============
class MessageType:
    MINT = 0          # 在对端铸造镜像酒证
    BURN = 1          # 在对端销毁镜像酒证
    PLEDGE = 2        # 在对端完成跨链质押
    UNPLEDGE = 3      # 在对端完成跨链解锁
    PLEDGE_INIT = 4   # 源链发起跨链质押（携带承诺）
    STATE_QUERY = 5   # 跨链状态查询


# ============ 消息编解码 ============
def encode_message(msg: Dict[str, Any]) -> bytes:
    """
    将跨链消息编码为字节流（在源链事件 log 中以 hex 形式出现）。
    字段顺序（与合约 CrossChainMessage 一致）：
      messageType, sourceChainId, sourceBlockNumber, sourceTxHash,
      sourceOwner, targetRecipient, sourceCertId, targetCertId,
      warehouseCode, vintage, valuation, pledgeAmount, nonce,
      merkleRoot, leafIndex, timestamp
    """
    parts = []
    parts.append(msg['messageType'].to_bytes(1, 'big'))
    parts.append(msg['sourceChainId'].to_bytes(8, 'big'))
    parts.append(msg['sourceBlockNumber'].to_bytes(8, 'big'))
    parts.append(bytes.fromhex(msg['sourceTxHash'][2:] if msg['sourceTxHash'].startswith('0x') else msg['sourceTxHash']))
    parts.append(bytes.fromhex(msg['sourceOwner'][2:] if msg['sourceOwner'].startswith('0x') else msg['sourceOwner']))
    parts.append(bytes.fromhex(msg['targetRecipient'][2:] if msg['targetRecipient'].startswith('0x') else msg['targetRecipient']))
    parts.append(bytes.fromhex(msg['sourceCertId'][2:] if msg['sourceCertId'].startswith('0x') else msg['sourceCertId']))
    parts.append(bytes.fromhex(msg['targetCertId'][2:] if msg['targetCertId'].startswith('0x') else msg['targetCertId']))
    wc = msg['warehouseCode'].encode('utf-8')
    parts.append(len(wc).to_bytes(2, 'big') + wc)
    parts.append(msg['vintage'].to_bytes(2, 'big'))
    parts.append(msg['valuation'].to_bytes(32, 'big'))
    parts.append(msg['pledgeAmount'].to_bytes(32, 'big'))
    parts.append(msg['nonce'].to_bytes(32, 'big'))
    parts.append(bytes.fromhex(msg['merkleRoot'][2:] if msg['merkleRoot'].startswith('0x') else msg['merkleRoot']))
    parts.append(msg['leafIndex'].to_bytes(8, 'big'))
    parts.append(int(time.time()).to_bytes(8, 'big'))
    return b''.join(parts)


def message_hash(msg: Dict[str, Any]) -> str:
    """计算消息哈希（keccak256）"""
    encoded = encode_message(msg)
    h = keccak(encoded)
    return '0x' + h.hex()


# ============ Merkle 树（事件树）============
class MerkleTree:
    """
    简化的 Merkle 树实现 - 用于演示事件可追溯性验证
    实际链上状态树使用 Patricia Trie，本项目为了演示实现简单的二进制 Merkle 树
    """

    def __init__(self, leaves: List[bytes]):
        self.leaves = leaves
        if not leaves:
            self.root = b'\x00' * 32
            self.layers = [[b'\x00' * 32]]
            return
        self.layers = [list(leaves)]
        while len(self.layers[-1]) > 1:
            prev = self.layers[-1]
            new_layer = []
            for i in range(0, len(prev), 2):
                if i + 1 < len(prev):
                    new_layer.append(keccak(prev[i] + prev[i+1]))
                else:
                    new_layer.append(keccak(prev[i] + prev[i]))  # 复制最后一个
            self.layers.append(new_layer)
        self.root = self.layers[-1][0]

    def get_proof(self, index: int) -> List[bytes]:
        """获取第 index 个叶子节点的 Merkle 证明"""
        proof = []
        for layer in self.layers[:-1]:
            pair_index = index ^ 1  # 兄弟节点
            if pair_index < len(layer):
                proof.append(layer[pair_index])
            index = index // 2
        return proof

    def get_root(self) -> bytes:
        return self.root

    def get_root_hex(self) -> str:
        return '0x' + self.root.hex()

    def verify(self, leaf: bytes, index: int, proof: List[bytes], root: bytes) -> bool:
        """验证 Merkle 证明"""
        computed = leaf
        for p in proof:
            if index % 2 == 0:
                computed = keccak(computed + p)
            else:
                computed = keccak(p + computed)
            index = index // 2
        return computed == root

    def get_leaf(self, index: int) -> bytes:
        return self.leaves[index]


def build_event_merkle_tree(event_hashes: List[str]) -> MerkleTree:
    """
    从一个区块中所有事件的 keccak256 哈希构建 Merkle 树
    event_hashes: 形如 '0x...' 的 hex 字符串列表
    """
    leaves = [bytes.fromhex(h[2:] if h.startswith('0x') else h) for h in event_hashes]
    return MerkleTree(leaves)


def verify_single_leaf_merkle(leaf: str, root: str) -> bool:
    """
    Single-leaf Merkle tree verification.

    When a block contains exactly one event (e.g. our bridge message),
    the event root is just the hash of that one event. This is the
    "single-leaf" simplification used in the demo; production should
    use full Merkle path verification (see ``MerkleTree.verify``).

    Args:
        leaf: 0x-prefixed 32-byte hex hash
        root: 0x-prefixed 32-byte hex hash (the event root from block header)

    Returns:
        True iff leaf == root.
    """
    if isinstance(leaf, str):
        leaf = leaf.lower()
    if isinstance(root, str):
        root = root.lower()
    return leaf == root


# ============ 重放保护 ============
class ReplayProtector:
    """
    跨链重放保护 - 在中继器侧维护
    1. 跟踪已处理消息（按 messageHash）
    2. 跟踪已用 nonce（每条链独立计数）
    3. 检测同 nonce 跨链重复
    """

    def __init__(self):
        self.processed_messages: Dict[str, Dict] = {}  # messageHash -> {chainId, blockNumber, processedAt}
        self.used_nonces: Dict[tuple, bool] = {}  # (chainId, nonce) -> True
        self.tx_hashes: Dict[str, set] = {}  # chainId -> set of txHash

    def check_and_record(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        检查重放，返回:
          - 'OK': 通过
          - 'MSG_REPLAY': 消息哈希已处理
          - 'NONCE_REPLAY': nonce 已用
          - 'TX_REPLAY': 源链 txHash 已处理
        """
        mh = msg.get('messageHash')
        nonce = msg.get('nonce')
        chain_id = msg.get('sourceChainId')
        txh = msg.get('sourceTxHash')
        if not all([mh, nonce is not None, chain_id, txh]):
            return {'status': 'INVALID', 'reason': 'Missing fields'}

        if mh in self.processed_messages:
            return {'status': 'MSG_REPLAY', 'reason': f'message {mh[:18]}... already processed at block {self.processed_messages[mh]["blockNumber"]}'}

        nonce_key = (chain_id, nonce)
        if nonce_key in self.used_nonces:
            return {'status': 'NONCE_REPLAY', 'reason': f'nonce {nonce} on chain {chain_id} already used'}

        chain_tx_set = self.tx_hashes.setdefault(chain_id, set())
        if txh in chain_tx_set:
            return {'status': 'TX_REPLAY', 'reason': f'txHash {txh[:18]}... already processed'}

        # 记录
        self.processed_messages[mh] = {
            'chainId': chain_id,
            'blockNumber': msg.get('sourceBlockNumber'),
            'processedAt': time.time(),
        }
        self.used_nonces[nonce_key] = True
        chain_tx_set.add(txh)
        return {'status': 'OK', 'recorded': True}

    def get_stats(self) -> Dict[str, Any]:
        return {
            'processedMessages': len(self.processed_messages),
            'usedNonces': len(self.used_nonces),
            'trackedChains': list(self.tx_hashes.keys()),
        }


# ============ 最终性确认 ============
class FinalityTracker:
    """
    最终性跟踪 - 中继器在提交跨链消息前必须等待源链达到最终性
    简化策略：
      - PoA 类联盟链（BCOS, ChainMaker）默认单块出块即最终
      - 设置 minimumConfirmations 参数
    """

    def __init__(self, minimum_confirmations: int = 1):
        self.minimum_confirmations = minimum_confirmations
        # chainId -> {blockNumber -> {hash, timestamp, observedAt}}
        self.blocks: Dict[int, Dict[int, Dict]] = {}
        self._last_observed: Dict[int, int] = {}

    def observe_block(self, chain_id: int, block_number: int, block_hash: str):
        if chain_id not in self.blocks:
            self.blocks[chain_id] = {}
        self.blocks[chain_id][block_number] = {
            'hash': block_hash,
            'observedAt': time.time(),
        }
        last = self._last_observed.get(chain_id, 0)
        if block_number > last:
            self._last_observed[chain_id] = block_number

    def is_finalized(self, chain_id: int, block_number: int) -> bool:
        last = self._last_observed.get(chain_id, 0)
        if last == 0:
            return False
        return (last - block_number) >= self.minimum_confirmations

    def get_confirmations(self, chain_id: int, block_number: int) -> int:
        last = self._last_observed.get(chain_id, 0)
        return max(0, last - block_number + 1)

    def get_stats(self) -> Dict[str, Any]:
        return {
            'minimumConfirmations': self.minimum_confirmations,
            'trackedChains': {
                cid: {'blocks': len(blocks), 'latest': self._last_observed.get(cid, 0)}
                for cid, blocks in self.blocks.items()
            }
        }


if __name__ == '__main__':
    # 单元测试
    print("=== 单元测试 ===")
    # 1. 编解码
    msg = {
        'messageType': 0,
        'sourceChainId': 1001,
        'sourceBlockNumber': 42,
        'sourceTxHash': '0x' + '11' * 32,
        'sourceOwner': '0x' + '22' * 20,
        'targetRecipient': '0x' + '33' * 20,
        'sourceCertId': '0x' + '44' * 32,
        'targetCertId': '0x' + '55' * 32,
        'warehouseCode': 'GZJJ-2024-001',
        'vintage': 2024,
        'valuation': 10**18,
        'pledgeAmount': 0,
        'nonce': 1,
        'merkleRoot': '0x' + '66' * 32,
        'leafIndex': 0,
    }
    mh = message_hash(msg)
    print(f"消息哈希: {mh}")

    # 2. Merkle
    leaves = [keccak(b'event1'), keccak(b'event2'), keccak(b'event3'), keccak(b'event4')]
    tree = MerkleTree(leaves)
    print(f"Merkle 根: {tree.get_root_hex()}")
    proof = tree.get_proof(2)
    leaf = tree.get_leaf(2)
    valid = tree.verify(leaf, 2, proof, tree.get_root())
    print(f"验证 leaf[2]: {valid}")
    assert valid

    # 3. 重放保护
    rp = ReplayProtector()
    msg['messageHash'] = mh
    print(f"首次检查: {rp.check_and_record(msg)}")
    print(f"二次检查: {rp.check_and_record(msg)}")  # 应为 MSG_REPLAY

    # 4. 最终性
    ft = FinalityTracker(minimum_confirmations=1)
    for i in range(1, 11):
        ft.observe_block(1001, i, f'0x{i:064x}')
    print(f"块 5 在 10 确认后是否最终: {ft.is_finalized(1001, 5)}")
    print(f"块 9 是否最终: {ft.is_finalized(1001, 9)}")
    print(f"块 10 是否最终: {ft.is_finalized(1001, 10)}")
