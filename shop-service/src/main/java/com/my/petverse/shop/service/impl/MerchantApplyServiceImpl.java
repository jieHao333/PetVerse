package com.my.petverse.shop.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.shop.MerchantApplyAuditDTO;
import com.my.petverse.common.dto.shop.MerchantApplyPageQueryDTO;
import com.my.petverse.common.dto.shop.MerchantApplySaveDTO;
import com.my.petverse.common.dto.shop.MerchantApplyUpdateDTO;
import com.my.petverse.common.entity.shop.Merchant;
import com.my.petverse.common.entity.shop.MerchantApply;
import com.my.petverse.common.enums.MerchantApplyStatus;
import com.my.petverse.common.enums.UserRole;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.shop.MerchantApplyVO;
import com.my.petverse.common.vo.user.UserVO;
import com.my.petverse.shop.feign.UserFeignClient;
import com.my.petverse.shop.mapper.MerchantApplyMapper;
import com.my.petverse.shop.mapper.MerchantMapper;
import com.my.petverse.shop.service.MerchantApplyService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 商家入驻申请服务实现类
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class MerchantApplyServiceImpl extends ServiceImpl<MerchantApplyMapper, MerchantApply>
        implements MerchantApplyService {

    private final MerchantMapper merchantMapper;

    private final UserFeignClient userFeignClient;

    @Override
    public MerchantApplyVO submit(Long userId, MerchantApplySaveDTO dto) {
        // 已是商家不允许重复入驻
        if (merchantMapper.selectCount(new LambdaQueryWrapper<Merchant>()
                .eq(Merchant::getUserId, userId)) > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "您已是入驻商家，无需重复申请");
        }
        // 同一用户同时只能有一条待审核申请
        if (count(new LambdaQueryWrapper<MerchantApply>()
                .eq(MerchantApply::getUserId, userId)
                .eq(MerchantApply::getStatus, MerchantApplyStatus.PENDING.getCode())) > 0) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "您已有待审核的入驻申请，请耐心等待审批");
        }
        MerchantApply apply = new MerchantApply();
        BeanUtils.copyProperties(dto, apply);
        apply.setUserId(userId);
        apply.setStatus(MerchantApplyStatus.PENDING.getCode());
        save(apply);
        return toVO(apply);
    }

    @Override
    public MerchantApplyVO resubmit(Long userId, MerchantApplyUpdateDTO dto) {
        MerchantApply apply = getById(dto.getId());
        if (apply == null || !Objects.equals(apply.getUserId(), userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "申请单不存在");
        }
        // 仅已驳回的申请可以修改后重新提交
        if (apply.getStatus() == null
                || apply.getStatus() != MerchantApplyStatus.REJECTED.getCode()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅已驳回的申请可以重新提交");
        }
        BeanUtils.copyProperties(dto, apply);
        apply.setStatus(MerchantApplyStatus.PENDING.getCode());
        apply.setRejectReason(null);
        apply.setAuditUserId(null);
        apply.setAuditTime(null);
        updateById(apply);
        return toVO(apply);
    }

    @Override
    public MerchantApplyVO getMine(Long userId) {
        MerchantApply apply = getOne(new LambdaQueryWrapper<MerchantApply>()
                .eq(MerchantApply::getUserId, userId)
                .orderByDesc(MerchantApply::getCreateTime)
                .last("limit 1"));
        return apply == null ? null : toVO(apply);
    }

    @Override
    public PageResult<MerchantApplyVO> pageForAdmin(MerchantApplyPageQueryDTO dto) {
        Page<MerchantApply> page = page(new Page<>(dto.getPageNum(), dto.getPageSize()),
                new LambdaQueryWrapper<MerchantApply>()
                        .eq(dto.getStatus() != null, MerchantApply::getStatus, dto.getStatus())
                        .like(StringUtils.hasText(dto.getShopName()), MerchantApply::getShopName, dto.getShopName())
                        .orderByAsc(MerchantApply::getStatus)
                        .orderByDesc(MerchantApply::getCreateTime));
        List<MerchantApplyVO> vos = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        fillApplicantNickname(vos);
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), vos);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void audit(Long adminId, MerchantApplyAuditDTO dto) {
        MerchantApply apply = getById(dto.getId());
        if (apply == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "申请单不存在");
        }
        // 只允许审批待审核状态，防止重复审批
        if (apply.getStatus() == null
                || apply.getStatus() != MerchantApplyStatus.PENDING.getCode()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "申请单已被审批，请刷新列表");
        }
        boolean approved = Boolean.TRUE.equals(dto.getApproved());
        if (!approved && !StringUtils.hasText(dto.getRejectReason())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "驳回时必须填写驳回原因");
        }
        apply.setStatus(approved
                ? MerchantApplyStatus.APPROVED.getCode()
                : MerchantApplyStatus.REJECTED.getCode());
        apply.setRejectReason(approved ? null : dto.getRejectReason().trim());
        apply.setAuditUserId(adminId);
        apply.setAuditTime(LocalDateTime.now());
        updateById(apply);

        if (approved) {
            // 幂等：防止同一用户重复开店
            if (merchantMapper.selectCount(new LambdaQueryWrapper<Merchant>()
                    .eq(Merchant::getUserId, apply.getUserId())) > 0) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "该用户已是入驻商家");
            }
            Merchant merchant = new Merchant();
            merchant.setUserId(apply.getUserId());
            merchant.setApplyId(apply.getId());
            merchant.setShopName(apply.getShopName());
            merchant.setDescription(apply.getDescription());
            merchant.setContactPhone(apply.getContactPhone());
            merchant.setStatus(1);
            merchantMapper.insert(merchant);
            // 跨服务升级用户角色，失败抛异常回滚本地事务，保证审批结果与角色一致
            Result<Boolean> result = userFeignClient.upgradeRole(apply.getUserId(), UserRole.MERCHANT.name());
            if (result == null || result.getCode() != ResultCode.SUCCESS.getCode()
                    || !Boolean.TRUE.equals(result.getData())) {
                throw new BusinessException(ResultCode.INTERNAL_ERROR, "升级商家角色失败，请稍后重试");
            }
        }
    }

    /** 批量聚合申请人昵称，用户服务不可用时降级为不展示 */
    private void fillApplicantNickname(List<MerchantApplyVO> vos) {
        if (vos.isEmpty()) {
            return;
        }
        Map<Long, MerchantApplyVO> pending = vos.stream()
                .collect(Collectors.toMap(MerchantApplyVO::getUserId, Function.identity(), (a, b) -> a));
        pending.forEach((userId, vo) -> {
            try {
                Result<UserVO> result = userFeignClient.getUserById(userId);
                if (result != null && result.getCode() == ResultCode.SUCCESS.getCode()
                        && result.getData() != null) {
                    String nickname = result.getData().getNickname();
                    vos.stream()
                            .filter(item -> Objects.equals(item.getUserId(), userId))
                            .forEach(item -> item.setApplicantNickname(nickname));
                }
            } catch (Exception e) {
                log.warn("查询申请人信息失败 userId={}", userId, e);
            }
        });
    }

    /** DO 转 VO */
    private MerchantApplyVO toVO(MerchantApply apply) {
        MerchantApplyVO vo = new MerchantApplyVO();
        BeanUtils.copyProperties(apply, vo);
        return vo;
    }
}
