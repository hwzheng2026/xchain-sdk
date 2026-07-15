// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "./LiquorCertificate.sol";
import "./LiquorPledge.sol";

/// @title CrossChainBridge - 跨链桥合约（接收端 + 验证）
/// @notice 5 维度验证在这里统一实现：
///   1. 资产映射一致性
///   2. Merkle 事件证明
///   3. 跨链身份认证
///   4. 重放保护
///   5. 最终性确认
contract CrossChainBridge {
    // ============ 依赖 ============
    LiquorCertificate public certContract;
    LiquorPledge public pledgeContract;

    address public owner;
    address public relayer; // 唯一的中继器地址

    // ============ 跨链配置 ============
    uint256 public sourceChainId;     // 本合约所在链的 ID
    uint256 public peerChainId;       // 对端链 ID
    bytes32 public peerBridgeAddress; // 对端桥合约地址（hash 形式）

    // ============ 验证维度 1: 资产映射一致性 ============
    // 锁定的源链 certId => 目标链镜像 certId
    mapping(bytes32 => bytes32) public sourceToTarget;
    // 目标链镜像 certId => 源链 certId
    mapping(bytes32 => bytes32) public targetToSource;
    // 总额锁定跟踪
    uint256 public totalLockedValue;
    uint256 public totalIssuedValue;

    // ============ 验证维度 2: Merkle 证明 ============
    // 存储源链 block header 信息（由中继器提交）
    // 注：这里 stateRoot 实际用作"事件树根"（eventRoot），简化命名
    // 真实生产中应分别存 stateRoot 和 eventsRoot
    struct BlockHeader {
        bytes32 blockHash;
        bytes32 eventRoot;       // 简化的"事件树根"
        uint64  blockNumber;
        uint64  timestamp;
        bool    finalized;
    }
    mapping(uint256 => BlockHeader) public blockHeaders; // blockNumber => header
    mapping(bytes32 => bool) public processedTxHashes;   // 防止同一交易被多次处理

    // ============ 验证维度 3: 跨链身份 ============
    mapping(address => bool) public authorizedSigners;  // 已授权的跨链签名者
    mapping(bytes32 => bool) public processedMessages;  // 消息哈希去重（防止重放）

    // ============ 验证维度 4: 重放保护 ============
    uint256 public nonce;
    mapping(uint256 => bool) public usedNonces;         // nonce 已使用
    mapping(bytes32 => uint256) public messageNonces;   // 消息哈希 => nonce

    // ============ 验证维度 5: 最终性 ============
    uint256 public requiredConfirmations; // 需要的确认数
    mapping(bytes32 => uint256) public messageConfirmations; // 消息哈希 => 已确认数

    // ============ 事件 ============
    event CrossChainMint(
        bytes32 indexed sourceCertId,
        bytes32 indexed targetCertId,
        uint256 indexed sourceChainId,
        bytes32 sourceTxHash,
        uint256 sourceBlockNumber,
        address holder
    );

    event CrossChainBurn(
        bytes32 indexed sourceCertId,
        bytes32 indexed targetCertId,
        uint256 indexed sourceChainId,
        bytes32 sourceTxHash
    );

    event MessageProcessed(
        bytes32 indexed messageHash,
        bytes32 indexed sourceTxHash,
        uint256 sourceBlockNumber,
        uint256 confirmations
    );

    event FinalityRecorded(
        uint256 indexed blockNumber,
        bytes32 blockHash,
        bytes32 stateRoot
    );

    event RelayerUpdated(address indexed oldRelayer, address indexed newRelayer);

    event ReplayDetected(bytes32 indexed messageHash, address indexed reporter);

    // ============ 修饰符 ============
    modifier onlyOwner() { require(msg.sender == owner, "NOT_OWNER"); _; }
    modifier onlyRelayer() { require(msg.sender == relayer, "NOT_RELAYER"); _; }

    // ============ 构造函数 ============
    constructor(
        address _certContract,
        address _pledgeContract,
        uint256 _sourceChainId,
        uint256 _peerChainId,
        uint256 _requiredConfirmations
    ) {
        owner = msg.sender;
        relayer = msg.sender; // 部署者初始为 relayer
        certContract = LiquorCertificate(_certContract);
        pledgeContract = LiquorPledge(_pledgeContract);
        sourceChainId = _sourceChainId;
        peerChainId = _peerChainId;
        requiredConfirmations = _requiredConfirmations;
    }

    // ============ 管理员接口 ============
    function setRelayer(address _relayer) external onlyOwner {
        emit RelayerUpdated(relayer, _relayer);
        relayer = _relayer;
    }

    function setRequiredConfirmations(uint256 _n) external onlyOwner {
        requiredConfirmations = _n;
    }

    function authorizeSigner(address signer) external onlyOwner {
        authorizedSigners[signer] = true;
    }

    function setPeerBridge(bytes32 _peerBridgeAddress) external onlyOwner {
        peerBridgeAddress = _peerBridgeAddress;
    }

    // ============ 验证维度 5: 提交并确认源链 block header ============
    function submitBlockHeader(
        uint256 blockNumber,
        bytes32 blockHash,
        bytes32 eventRoot,         // 事件树根（简化的"eventsRoot"）
        uint64 timestamp
    ) external onlyRelayer {
        BlockHeader storage h = blockHeaders[blockNumber];
        h.blockHash = blockHash;
        h.eventRoot = eventRoot;
        h.blockNumber = uint64(blockNumber);
        h.timestamp = timestamp;
        // 简化：每个 header 提交后立即算作 1 个确认
        // 实际中需要累加
        h.finalized = true;
        emit FinalityRecorded(blockNumber, blockHash, eventRoot);
    }

    // ============ 验证维度 2 + 5: 验证 Merkle 证明 ============
    function verifyMerkleProof(
        bytes32 leaf,
        bytes32[] calldata proof,
        uint256 index,
        bytes32 root
    ) public pure returns (bool) {
        bytes32 computed = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 proofElement = proof[i];
            if (index % 2 == 0) {
                computed = keccak256(abi.encodePacked(computed, proofElement));
            } else {
                computed = keccak256(abi.encodePacked(proofElement, computed));
            }
            index = index / 2;
        }
        return computed == root;
    }

    /// @notice 验证 messageHash 是 eventRoot 的唯一叶子（演示用单叶子证明）
    /// 真实生产中应使用完整 Merkle path
    function verifySingleLeafProof(
        bytes32 leaf,
        bytes32 root
    ) public pure returns (bool) {
        // 简化：单叶子树的根 == 叶子本身
        return leaf == root;
    }

    // ============ 验证维度 3 + 4: 处理跨链消息 ============
    struct CrossChainMessage {
        bytes32 messageHash;
        uint256 messageType;       // 0=mint, 1=burn, 2=pledge, 3=unpledge
        uint256 sourceChainId;
        uint256 sourceBlockNumber;
        bytes32 sourceTxHash;
        address sourceOwner;
        address targetRecipient;
        bytes32 sourceCertId;
        bytes32 targetCertId;
        string warehouseCode;
        uint16 vintage;
        uint256 valuation;
        uint256 pledgeAmount;
        uint256 nonce;
        bytes32 merkleRoot;
        bytes32[] merkleProof;
        uint256 leafIndex;
    }

    function processMessage(CrossChainMessage calldata msg_) external payable onlyRelayer {
        // ---- 维度 4: 重放保护 ----
        require(!processedMessages[msg_.messageHash], "REPLAY");
        require(msg_.sourceChainId == peerChainId, "BAD_CHAIN_ID");
        require(!usedNonces[msg_.nonce], "NONCE_USED");

        // ---- 维度 5: 最终性 ----
        BlockHeader storage header = blockHeaders[msg_.sourceBlockNumber];
        require(header.finalized, "NOT_FINALIZED");
        require(header.blockHash != bytes32(0), "NO_HEADER");

        // ---- 维度 2: Merkle 证明 ----
        // 验证 messageHash == eventRoot（演示用单叶子证明）
        // 真实生产中应使用完整 Merkle path: verifyMerkleProof(...)
        require(verifySingleLeafProof(msg_.messageHash, header.eventRoot), "MERKLE_FAIL");

        // ---- 维度 3: 跨链身份 ----
        // 允许：源 owner 是授权签名者，或者 sourceOwner == 0x0（演示场景）
        // 真实生产：sourceOwner 应该是源链上的 EOA 或合约地址，必须在目标链上有映射
        if (msg_.sourceOwner != address(0x0)) {
            require(authorizedSigners[msg_.sourceOwner], "UNAUTH");
        }

        // 标记为已处理
        processedMessages[msg_.messageHash] = true;
        usedNonces[msg_.nonce] = true;
        messageNonces[msg_.messageHash] = msg_.nonce;
        processedTxHashes[msg_.sourceTxHash] = true;
        messageConfirmations[msg_.messageHash] = requiredConfirmations;

        // 根据消息类型执行
        if (msg_.messageType == 0) {
            // Mint: 铸造镜像酒证
            _processMint(msg_);
        } else if (msg_.messageType == 1) {
            // Burn: 销毁镜像酒证
            _processBurn(msg_);
        } else if (msg_.messageType == 2) {
            // Pledge: 跨链质押完成
            _processPledge(msg_);
        } else if (msg_.messageType == 3) {
            // Unpledge: 跨链解锁
            _processUnpledge(msg_);
        } else {
            revert("UNKNOWN_TYPE");
        }

        emit MessageProcessed(
            msg_.messageHash,
            msg_.sourceTxHash,
            msg_.sourceBlockNumber,
            requiredConfirmations
        );
    }

    function _processMint(CrossChainMessage calldata msg_) internal {
        require(sourceToTarget[msg_.sourceCertId] == bytes32(0), "ALREADY_MAPPED");
        // 铸造镜像酒证
        certContract.mintMirror(
            msg_.targetRecipient,
            msg_.targetCertId,
            msg_.warehouseCode,
            msg_.vintage,
            msg_.valuation,
            msg_.sourceCertId, // 用 sourceCertId 作为 crossChainHash
            msg_.sourceChainId,
            msg_.nonce           // 用 nonce 作为 origin serial
        );
        sourceToTarget[msg_.sourceCertId] = msg_.targetCertId;
        targetToSource[msg_.targetCertId] = msg_.sourceCertId;
        totalIssuedValue += msg_.valuation;
        emit CrossChainMint(
            msg_.sourceCertId,
            msg_.targetCertId,
            msg_.sourceChainId,
            msg_.sourceTxHash,
            msg_.sourceBlockNumber,
            msg_.targetRecipient
        );
    }

    function _processBurn(CrossChainMessage calldata msg_) internal {
        bytes32 targetCert = sourceToTarget[msg_.sourceCertId];
        require(targetCert != bytes32(0), "NOT_MAPPED");
        certContract.burn(targetCert);
        delete sourceToTarget[msg_.sourceCertId];
        delete targetToSource[targetCert];
        emit CrossChainBurn(msg_.sourceCertId, targetCert, msg_.sourceChainId, msg_.sourceTxHash);
    }

    function _processPledge(CrossChainMessage calldata msg_) internal {
        bytes32 targetCert = sourceToTarget[msg_.sourceCertId];
        require(targetCert != bytes32(0), "NOT_MAPPED");
        pledgeContract.completeCrossChainPledge{value: msg_.pledgeAmount}(
            msg_.sourceCertId,
            targetCert,
            msg_.sourceOwner,
            msg_.targetRecipient, // pledgee
            msg_.pledgeAmount,
            msg_.sourceChainId,
            msg_.messageHash
        );
    }

    function _processUnpledge(CrossChainMessage calldata msg_) internal {
        bytes32 targetCert = sourceToTarget[msg_.sourceCertId];
        require(targetCert != bytes32(0), "NOT_MAPPED");
        pledgeContract.completeCrossChainUnpledge(targetCert);
    }

    // ============ 报告重放攻击（社区监督）============
    function reportReplay(bytes32 messageHash) external {
        if (processedMessages[messageHash]) {
            emit ReplayDetected(messageHash, msg.sender);
        }
    }

    // ============ 查询接口 ============
    function isMessageProcessed(bytes32 messageHash) external view returns (bool) {
        return processedMessages[messageHash];
    }

    function isNonceUsed(uint256 n) external view returns (bool) {
        return usedNonces[n];
    }

    function isFinalized(uint256 blockNumber) external view returns (bool) {
        return blockHeaders[blockNumber].finalized;
    }

    function getBlockHeader(uint256 blockNumber) external view returns (BlockHeader memory) {
        return blockHeaders[blockNumber];
    }

    function getMapping(bytes32 sourceCertId) external view returns (bytes32) {
        return sourceToTarget[sourceCertId];
    }
}
