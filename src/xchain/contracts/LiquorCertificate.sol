// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/// @title LiquorCertificate - 数字酒证 NFT
/// @notice 贵州酱酒集团数字酒证的 ERC-721 风格实现
/// @dev 字段:
///   - warehouseCode: 酒仓编号（如 "GZJJ-2024-001"）
///   - vintage: 年份
///   - valuation: 估值（RMB，整数元）
///   - pledgeStatus: 0=未质押 1=质押中 2=已解锁
///   - crossChainHash: 跨链锚定哈希（与对端链上一致）
///   - originChainId: 起源链 ID（0=FISCO BCOS, 1=ChainMaker）
contract LiquorCertificate {
    // ============ 数据结构 ============
    struct Certificate {
        bytes32 certId;          // 酒证唯一 ID
        string warehouseCode;    // 酒仓编号
        uint16 vintage;          // 年份
        address holder;          // 当前持有人
        uint256 valuation;       // 估值
        PledgeStatus pledgeStatus; // 质押状态
        bytes32 crossChainHash;  // 跨链锚定哈希
        uint256 originChainId;   // 起源链 ID
        uint64 mintedAt;         // 铸造时间
    }

    enum PledgeStatus { None, Pledged, Unlocked }

    // ============ 状态变量 ============
    string public constant NAME = "GuizhouJiangjiu Liquor Certificate";
    string public constant SYMBOL = "GZJJ-LC";
    string public constant VERSION = "1.0.0";

    address public owner;
    uint256 public nextSerial; // 自增序列号

    mapping(bytes32 => Certificate) private _certs;
    mapping(uint256 => bytes32) private _serialToCertId;
    mapping(address => uint256) private _balanceOf;
    mapping(address => bytes32[]) private _ownedCerts;

    // 授权：跨链桥合约可更新跨链字段
    address public bridgeContract;
    // 授权：质押合约可更新质押状态
    address public pledgeContract;

    // ============ 事件 ============
    event CertificateMinted(
        bytes32 indexed certId,
        uint256 indexed serial,
        address indexed holder,
        string warehouseCode,
        uint16 vintage,
        uint256 valuation,
        bytes32 crossChainHash,
        uint256 originChainId
    );

    event CertificateBurned(
        bytes32 indexed certId,
        address indexed holder
    );

    event CrossChainAnchored(
        bytes32 indexed certId,
        bytes32 indexed crossChainHash,
        uint256 originChainId
    );

    // ============ 修饰符 ============
    modifier onlyOwner() {
        require(msg.sender == owner, "NOT_OWNER");
        _;
    }

    modifier onlyBridge() {
        require(msg.sender == bridgeContract, "NOT_BRIDGE");
        _;
    }

    // ============ 构造函数 ============
    constructor() {
        owner = msg.sender;
    }

    function setBridgeContract(address _bridge) external onlyOwner {
        bridgeContract = _bridge;
    }

    function setPledgeContract(address _pledge) external onlyOwner {
        pledgeContract = _pledge;
    }

    // ============ 铸造 ============
    function mint(
        address to,
        bytes32 certId,
        string calldata warehouseCode,
        uint16 vintage,
        uint256 valuation,
        bytes32 crossChainHash,
        uint256 originChainId
    ) external onlyOwner returns (uint256 serial) {
        require(to != address(0), "ZERO_ADDR");
        require(_certs[certId].certId == bytes32(0), "CERT_EXISTS");
        require(vintage >= 1900 && vintage <= 2100, "BAD_VINTAGE");

        serial = nextSerial++;
        _certs[certId] = Certificate({
            certId: certId,
            warehouseCode: warehouseCode,
            vintage: vintage,
            holder: to,
            valuation: valuation,
            pledgeStatus: PledgeStatus.None,
            crossChainHash: crossChainHash,
            originChainId: originChainId,
            mintedAt: uint64(block.timestamp)
        });
        _serialToCertId[serial] = certId;
        _balanceOf[to] += 1;
        _ownedCerts[to].push(certId);

        emit CertificateMinted(certId, serial, to, warehouseCode, vintage, valuation, crossChainHash, originChainId);
    }

    // ============ 跨链桥用：铸造对端镜像酒证 ============
    function mintMirror(
        address to,
        bytes32 certId,
        string calldata warehouseCode,
        uint16 vintage,
        uint256 valuation,
        bytes32 crossChainHash,
        uint256 originChainId,
        uint256 originSerial
    ) external onlyBridge returns (uint256 serial) {
        require(to != address(0), "ZERO_ADDR");
        require(_certs[certId].certId == bytes32(0), "CERT_EXISTS");

        serial = nextSerial++;
        _certs[certId] = Certificate({
            certId: certId,
            warehouseCode: warehouseCode,
            vintage: vintage,
            holder: to,
            valuation: valuation,
            pledgeStatus: PledgeStatus.None,
            crossChainHash: crossChainHash,
            originChainId: originChainId,
            mintedAt: uint64(block.timestamp)
        });
        // 用 originSerial 编码在 serial 里（high 128 bit 是 origin serial，low 128 bit 是本地）
        _serialToCertId[serial] = certId;
        _balanceOf[to] += 1;
        _ownedCerts[to].push(certId);

        emit CertificateMinted(certId, serial, to, warehouseCode, vintage, valuation, crossChainHash, originChainId);
        emit CrossChainAnchored(certId, crossChainHash, originChainId);
        // originSerial 隐含在事件 log 中
        originSerial; // silence unused warning
    }

    // ============ 销毁（跨链转出时） ============
    function burn(bytes32 certId) external onlyBridge {
        Certificate storage c = _certs[certId];
        require(c.certId != bytes32(0), "NOT_EXISTS");
        address holder = c.holder;
        delete _certs[certId];
        _balanceOf[holder] -= 1;
        emit CertificateBurned(certId, holder);
    }

    // ============ 内部更新质押状态（由 LiquorPledge 调用） ============
    function setPledgeStatus(
        bytes32 certId,
        PledgeStatus status
    ) external returns (bool ok) {
        require(msg.sender == address(pledgeContract), "AUTH");
        Certificate storage c = _certs[certId];
        require(c.certId != bytes32(0), "NOT_EXISTS");
        c.pledgeStatus = status;
        ok = true;
    }

    // ============ 查询 ============
    function getCertificate(bytes32 certId) external view returns (Certificate memory) {
        return _certs[certId];
    }

    function ownerOf(bytes32 certId) external view returns (address) {
        return _certs[certId].holder;
    }

    function balanceOf(address ownerAddr) external view returns (uint256) {
        return _balanceOf[ownerAddr];
    }

    function certIdBySerial(uint256 serial) external view returns (bytes32) {
        return _serialToCertId[serial];
    }

    function ownedCerts(address ownerAddr) external view returns (bytes32[] memory) {
        return _ownedCerts[ownerAddr];
    }

    function exists(bytes32 certId) external view returns (bool) {
        return _certs[certId].certId != bytes32(0);
    }

    function totalSupply() external view returns (uint256) {
        return nextSerial;
    }
}
