// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "./LiquorCertificate.sol";

/// @title LiquorPledge - 数字酒证质押管理
/// @notice 支持跨链质押（本地质押 + 跨链质押到对端）
contract LiquorPledge {
    LiquorCertificate public certContract;

    struct Pledge {
        bytes32 certId;        // 酒证 ID
        address pledger;       // 出质人
        address pledgee;       // 质权人
        uint256 amount;        // 质押金额（wei）
        uint64 startTime;      // 质押起始时间戳
        uint64 endTime;        // 解锁时间戳（0 表示未锁定期）
        bool active;           // 是否在押
    }

    mapping(bytes32 => Pledge) public pledges;       // certId => Pledge
    mapping(address => bytes32[]) public userPledges; // 持有人 => 在押的 certId 列表

    // 跨链桥合约
    address public bridgeContract;

    // 跨链质押表：源链 certId -> 对端 certId
    mapping(bytes32 => bytes32) public crossChainCertMap;

    // 事件
    event Pledged(
        bytes32 indexed certId,
        address indexed pledger,
        address indexed pledgee,
        uint256 amount,
        uint64 startTime
    );

    event Unpledged(
        bytes32 indexed certId,
        address indexed pledger,
        address indexed pledgee,
        uint256 amount,
        uint64 endTime
    );

    event CrossChainPledgeInitiated(
        bytes32 indexed sourceCertId,
        bytes32 indexed targetCertId,
        address indexed pledger,
        uint256 amount,
        uint256 targetChainId
    );

    event CrossChainPledgeCompleted(
        bytes32 indexed sourceCertId,
        bytes32 indexed targetCertId,
        bytes32 messageHash
    );

    modifier onlyBridge() {
        require(msg.sender == bridgeContract, "NOT_BRIDGE");
        _;
    }

    constructor(address _certContract) {
        certContract = LiquorCertificate(_certContract);
        bridgeContract = msg.sender; // 默认 owner 为桥
    }

    function setBridgeContract(address _bridge) external {
        require(msg.sender == bridgeContract, "AUTH");
        bridgeContract = _bridge;
    }

    /// @notice 本地质押
    function pledge(
        bytes32 certId,
        address pledgee,
        uint64 lockDuration
    ) external payable returns (uint256 amount) {
        amount = msg.value;
        require(amount > 0, "ZERO_AMOUNT");
        require(pledgee != address(0), "ZERO_PLEDGEE");
        require(certContract.ownerOf(certId) == msg.sender, "NOT_OWNER");
        require(!pledges[certId].active, "ALREADY_PLEDGED");

        uint64 startTime = uint64(block.timestamp);
        uint64 endTime = lockDuration > 0 ? startTime + lockDuration : 0;
        pledges[certId] = Pledge({
            certId: certId,
            pledger: msg.sender,
            pledgee: pledgee,
            amount: amount,
            startTime: startTime,
            endTime: endTime,
            active: true
        });
        userPledges[msg.sender].push(certId);

        certContract.setPledgeStatus(certId, LiquorCertificate.PledgeStatus.Pledged);

        emit Pledged(certId, msg.sender, pledgee, amount, startTime);
    }

    /// @notice 本地解锁
    function unpledge(bytes32 certId) external {
        Pledge storage p = pledges[certId];
        require(p.active, "NOT_PLEDGED");
        require(p.pledger == msg.sender, "NOT_PLEDGED_BY_YOU");
        if (p.endTime > 0) {
            require(block.timestamp >= p.endTime, "STILL_LOCKED");
        }

        p.active = false;
        p.endTime = uint64(block.timestamp);
        uint256 amount = p.amount;
        address pledgee = p.pledgee;
        address pledger = p.pledger;

        // 把质押金额退还（实际场景退给质权人或按规则分配）
        (bool ok, ) = pledgee.call{value: amount}("");
        require(ok, "REFUND_FAIL");

        certContract.setPledgeStatus(certId, LiquorCertificate.PledgeStatus.Unlocked);

        emit Unpledged(certId, pledger, pledgee, amount, p.endTime);
    }

    /// @notice 发起跨链质押
    /// @dev 在源链上发起，目标链上将铸造镜像酒证并质押给同一 pledgee
    function initiateCrossChainPledge(
        bytes32 certId,
        address pledgee,
        uint256 targetChainId,
        uint64 lockDuration
    ) external payable returns (bytes32 messageHash) {
        require(targetChainId != block.chainid, "SAME_CHAIN");
        require(msg.value > 0, "ZERO_AMOUNT");
        require(certContract.ownerOf(certId) == msg.sender, "NOT_OWNER");
        require(!pledges[certId].active, "ALREADY_PLEDGED");

        // 锁源链酒证
        uint64 startTime = uint64(block.timestamp);
        uint64 endTime = lockDuration > 0 ? startTime + lockDuration : 0;
        pledges[certId] = Pledge({
            certId: certId,
            pledger: msg.sender,
            pledgee: pledgee,
            amount: msg.value,
            startTime: startTime,
            endTime: endTime,
            active: true
        });
        userPledges[msg.sender].push(certId);
        certContract.setPledgeStatus(certId, LiquorCertificate.PledgeStatus.Pledged);

        // 跨链消息哈希
        messageHash = keccak256(abi.encodePacked(
            "PLEDGE",
            certId,
            msg.sender,
            pledgee,
            msg.value,
            targetChainId,
            block.chainid,
            startTime
        ));

        emit CrossChainPledgeInitiated(certId, bytes32(0), msg.sender, msg.value, targetChainId);
        emit Pledged(certId, msg.sender, pledgee, msg.value, startTime);
    }

    /// @notice 跨链桥合约调用：在本链完成跨链质押的对端
    function completeCrossChainPledge(
        bytes32 sourceCertId,
        bytes32 targetCertId,
        address pledger,
        address pledgee,
        uint256 amount,
        uint256 sourceChainId,
        bytes32 messageHash
    ) external payable onlyBridge {
        require(sourceChainId != block.chainid, "SAME_CHAIN");
        require(msg.value == amount, "AMOUNT_MISMATCH");
        if (crossChainCertMap[sourceCertId] != bytes32(0)) {
            revert("MAPPED");
        }
        // 注: 跨链铸出的酒证是镜像是 holder=pledgee 形式，表示质押锁定
        crossChainCertMap[sourceCertId] = targetCertId;
        pledges[targetCertId] = Pledge({
            certId: targetCertId,
            pledger: pledger,
            pledgee: pledgee,
            amount: amount,
            startTime: uint64(block.timestamp),
            endTime: 0,
            active: true
        });

        emit CrossChainPledgeCompleted(sourceCertId, targetCertId, messageHash);
    }

    /// @notice 跨链解锁完成
    function completeCrossChainUnpledge(
        bytes32 targetCertId
    ) external onlyBridge {
        Pledge storage p = pledges[targetCertId];
        require(p.active, "NOT_ACTIVE");

        p.active = false;
        p.endTime = uint64(block.timestamp);
        certContract.setPledgeStatus(targetCertId, LiquorCertificate.PledgeStatus.Unlocked);
        emit Unpledged(targetCertId, p.pledger, p.pledgee, p.amount, p.endTime);
    }

    // ============ 查询 ============
    function getPledge(bytes32 certId) external view returns (Pledge memory) {
        return pledges[certId];
    }

    function isPledged(bytes32 certId) external view returns (bool) {
        return pledges[certId].active;
    }

    function getCrossChainMapping(bytes32 sourceCertId) external view returns (bytes32) {
        return crossChainCertMap[sourceCertId];
    }

    function getUserPledges(address user) external view returns (bytes32[] memory) {
        return userPledges[user];
    }
}
