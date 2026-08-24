package com.my.petverse.pet.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSetActiveDTO;
import com.my.petverse.common.dto.pet.PetSignInDTO;
import com.my.petverse.common.dto.pet.PetUpdateDTO;
import com.my.petverse.common.entity.pet.Pet;
import com.my.petverse.common.entity.pet.PetCatalog;
import com.my.petverse.common.enums.PetClaimMethod;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.util.PetLevelCalculator;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.pet.PetSignInVO;
import com.my.petverse.common.vo.pet.PetVO;
import com.my.petverse.pet.mapper.PetMapper;
import com.my.petverse.pet.service.PetCatalogService;
import com.my.petverse.pet.service.PetService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 宠物服务实现类
 */
@Service
@RequiredArgsConstructor
public class PetServiceImpl extends ServiceImpl<PetMapper, Pet> implements PetService {

    /** 签到基础经验值 */
    private static final long SIGN_IN_BASE_EXP = 20L;

    /** 连续签到每日额外经验值 */
    private static final long SIGN_IN_STREAK_EXP = 5L;

    private final PetCatalogService petCatalogService;

    /** 根据ID查询宠物 */
    @Override
    public PetVO getPetById(Long id) {
        Pet pet = getById(id);
        return pet == null ? null : toVO(pet);
    }

    /** 查询宠物列表 */
    @Override
    public List<PetVO> listPets() {
        return list().stream().map(this::toVO).collect(Collectors.toList());
    }

    /** 分页查询宠物 */
    @Override
    public PageResult<PetVO> pagePets(PetPageQueryDTO query) {
        LambdaQueryWrapper<Pet> wrapper = new LambdaQueryWrapper<Pet>()
                .like(StringUtils.hasText(query.getName()), Pet::getName, query.getName())
                .eq(StringUtils.hasText(query.getSpecies()), Pet::getSpecies, query.getSpecies())
                .eq(query.getUserId() != null, Pet::getUserId, query.getUserId())
                .orderByDesc(Pet::getCreateTime);
        Page<Pet> page = page(new Page<>(query.getPageNum(), query.getPageSize()), wrapper);
        List<PetVO> records = page.getRecords().stream().map(this::toVO).collect(Collectors.toList());
        return PageResult.of(page.getTotal(), page.getCurrent(), page.getSize(), records);
    }

    /** 新增宠物 */
    @Override
    public Long savePet(PetSaveDTO dto) {
        Pet pet = new Pet();
        BeanUtils.copyProperties(dto, pet);
        save(pet);
        return pet.getId();
    }

    /** 修改宠物 */
    @Override
    public boolean updatePet(PetUpdateDTO dto) {
        Pet pet = getById(dto.getId());
        if (pet == null) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        BeanUtils.copyProperties(dto, pet);
        return updateById(pet);
    }

    /** 删除宠物 */
    @Override
    public boolean deletePet(Long id) {
        return removeById(id);
    }

    /** 查询当前用户的出场宠物 */
    @Override
    public PetVO getMyPet(Long userId) {
        Pet pet = getActivePet(userId);
        return pet == null ? null : toVO(pet);
    }

    /** 查询当前用户的全部宠物，按领养先后排序 */
    @Override
    public List<PetVO> listMyPets(Long userId) {
        return listByUserId(userId).stream().map(this::toVO).collect(Collectors.toList());
    }

    /** 设置出场宠物：先清空该用户所有宠物的出场标记，再置目标宠物出场 */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public PetVO setActivePet(PetSetActiveDTO dto) {
        Pet pet = getById(dto.getPetId());
        if (pet == null || !pet.getUserId().equals(dto.getUserId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        update(new LambdaUpdateWrapper<Pet>()
                .eq(Pet::getUserId, dto.getUserId())
                .set(Pet::getActive, 0));
        pet.setActive(1);
        updateById(pet);
        return toVO(pet);
    }

    /**
     * 领取宠物
     * RANDOM 方式从图鉴随机抽取，CHOOSE 方式按图鉴ID自选
     * 支持领养多只：首只自动出场，后续宠物默认不出场
     */
    @Override
    public PetVO claimPet(PetClaimDTO dto) {
        boolean firstPet = count(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, dto.getUserId())) == 0;
        // 根据领取方式确定图鉴数据
        PetCatalog catalog;
        if (dto.getMethod() == PetClaimMethod.CHOOSE) {
            if (dto.getCatalogId() == null) {
                throw new BusinessException(ResultCode.BAD_REQUEST, "请选择要领取的宠物");
            }
            catalog = petCatalogService.getById(dto.getCatalogId());
            if (catalog == null) {
                throw new BusinessException(ResultCode.NOT_FOUND, "宠物图鉴不存在");
            }
        } else {
            catalog = petCatalogService.randomCatalogEntity();
        }

        // 根据图鉴数据初始化用户宠物，名字以用户取的为准，初始等级 1 级
        Pet pet = new Pet();
        pet.setUserId(dto.getUserId());
        pet.setName(dto.getName().trim());
        pet.setSpecies(catalog.getSpecies());
        pet.setBreed(catalog.getBreed());
        pet.setDescription(catalog.getDescription());
        pet.setImageUrl(catalog.getImageUrl());
        pet.setAge(1);
        pet.setLevel(1);
        pet.setExp(0L);
        pet.setSignStreak(0);
        pet.setActive(firstPet ? 1 : 0);
        save(pet);
        return toVO(pet);
    }

    /** 修改宠物名称，仅允许修改本人宠物 */
    @Override
    public PetVO renamePet(PetRenameDTO dto) {
        Pet pet = getById(dto.getPetId());
        if (pet == null || !pet.getUserId().equals(dto.getUserId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        pet.setName(dto.getName().trim());
        updateById(pet);
        return toVO(pet);
    }

    /**
     * 每日签到
     * 每天限签一次，为用户所有宠物统一发放递增经验并累计连续天数
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public PetSignInVO signIn(PetSignInDTO dto) {
        List<Pet> pets = listByUserId(dto.getUserId());
        if (pets.isEmpty()) {
            throw new BusinessException(ResultCode.NOT_FOUND, "用户尚未领养宠物");
        }
        LocalDate today = LocalDate.now();
        // 签到为整体行为，任一宠物已记录当天签到即拒绝
        if (pets.stream().anyMatch(p -> today.equals(p.getLastSignDate()))) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "今天已签到，请明天再来");
        }
        // 所有宠物同步签到，连续天数一致，任取一只计算即可
        Pet first = pets.get(0);
        boolean continuous = first.getLastSignDate() != null
                && first.getLastSignDate().equals(today.minusDays(1));
        int streak = continuous ? (first.getSignStreak() == null ? 0 : first.getSignStreak()) + 1 : 1;
        // 经验值 = 基础 20 + (连续天数-1) * 5
        long gainedExp = SIGN_IN_BASE_EXP + (long) (streak - 1) * SIGN_IN_STREAK_EXP;

        List<PetSignInVO.Item> items = new ArrayList<>();
        for (Pet pet : pets) {
            int levelBefore = pet.getLevel() == null ? 1 : pet.getLevel();
            PetLevelCalculator.gainExp(pet, gainedExp);
            pet.setSignStreak(streak);
            pet.setLastSignDate(today);
            PetSignInVO.Item item = new PetSignInVO.Item();
            item.setPetId(pet.getId());
            item.setName(pet.getName());
            item.setLevel(pet.getLevel());
            item.setExp(pet.getExp());
            item.setLeveledUp(pet.getLevel() > levelBefore);
            items.add(item);
        }
        updateBatchById(pets);

        PetSignInVO vo = new PetSignInVO();
        vo.setUserId(dto.getUserId());
        vo.setGainedExp(gainedExp);
        vo.setSignStreak(streak);
        vo.setPets(items);
        return vo;
    }

    /**
     * 按来源发放经验值，经验来源与奖励数值由 PetExpSource 枚举定义
     * 仅发放给当前出场宠物，用户无宠物时返回 null，不阻断上游业务流程
     */
    @Override
    public PetExpGainVO grantExp(PetExpGrantDTO dto) {
        // 解析经验来源，非法来源直接拒绝
        PetExpSource source;
        try {
            source = PetExpSource.valueOf(dto.getSource());
        } catch (IllegalArgumentException e) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "未知的经验来源：" + dto.getSource());
        }
        Pet pet = getActivePet(dto.getUserId());
        if (pet == null) {
            return null;
        }
        int levelBefore = pet.getLevel() == null ? 1 : pet.getLevel();
        // 累加经验并处理升级，满级后不再累积
        PetLevelCalculator.gainExp(pet, source.getExp());
        updateById(pet);

        // 组装发放结果
        PetExpGainVO vo = new PetExpGainVO();
        vo.setUserId(pet.getUserId());
        vo.setPetId(pet.getId());
        vo.setGainedExp(source.getExp());
        vo.setLevel(pet.getLevel());
        vo.setExp(pet.getExp());
        vo.setLeveledUp(pet.getLevel() > levelBefore);
        return vo;
    }

    /** 查询用户名下全部宠物，按领养先后排序 */
    private List<Pet> listByUserId(Long userId) {
        return list(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .orderByAsc(Pet::getCreateTime));
    }

    /** 查询用户当前出场宠物；兼容存量数据，无出场标记时取第一只 */
    private Pet getActivePet(Long userId) {
        Pet active = getOne(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .eq(Pet::getActive, 1)
                .last("limit 1"));
        if (active != null) {
            return active;
        }
        return getOne(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .orderByAsc(Pet::getCreateTime)
                .last("limit 1"));
    }

    /** DO 转 VO，补充升级所需经验与出场标记 */
    private PetVO toVO(Pet pet) {
        PetVO vo = new PetVO();
        BeanUtils.copyProperties(pet, vo);
        vo.setActive(pet.getActive() != null && pet.getActive() == 1);
        if (pet.getLevel() == null || pet.getLevel() >= PetLevelCalculator.MAX_LEVEL) {
            vo.setNextLevelExp(0L);
        } else {
            vo.setNextLevelExp(PetLevelCalculator.expNeededForNextLevel(pet.getLevel()));
        }
        return vo;
    }
}
