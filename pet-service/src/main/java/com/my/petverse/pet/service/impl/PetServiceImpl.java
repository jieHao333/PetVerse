package com.my.petverse.pet.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetHealthUpdateDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetProfileUpdateDTO;
import com.my.petverse.common.dto.pet.PetRegisterDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSignInDTO;
import com.my.petverse.common.dto.pet.PetUpdateDTO;
import com.my.petverse.common.entity.pet.Pet;
import com.my.petverse.common.entity.pet.PetCatalog;
import com.my.petverse.common.enums.PetClaimMethod;
import com.my.petverse.common.enums.PetExpSource;
import com.my.petverse.common.enums.PetGender;
import com.my.petverse.common.enums.PetType;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.oss.OssService;
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
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDate;
import java.time.Period;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
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

    /** 头像大小上限：2MB */
    private static final long MAX_AVATAR_SIZE = 2 * 1024 * 1024;

    /** 允许的头像内容类型，与用户头像保持一致 */
    private static final Set<String> AVATAR_CONTENT_TYPES = Set.of(
            "image/png", "image/jpeg", "image/jpg", "image/webp");

    /** OSS 存储路径中的日期目录格式 */
    private static final DateTimeFormatter AVATAR_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyy/MM/dd");

    private final PetCatalogService petCatalogService;

    private final OssService ossService;

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

    /** 删除宠物：先校验归属再逻辑删除，仅允许删除本人宠物 */
    @Override
    public boolean deletePet(Long petId, Long userId) {
        Pet pet = getById(petId);
        if (pet == null || !pet.getUserId().equals(userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        return removeById(petId);
    }

    /** 查询当前用户的代表宠物：优先第一只虚拟宠物，无则第一只真实宠物 */
    @Override
    public PetVO getMyPet(Long userId) {
        Pet pet = getRepresentPet(userId);
        return pet == null ? null : toVO(pet);
    }

    /** 查询当前用户的全部宠物，按领养先后排序 */
    @Override
    public List<PetVO> listMyPets(Long userId) {
        return listByUserId(userId).stream().map(this::toVO).collect(Collectors.toList());
    }

    /**
     * 领取虚拟宠物
     * RANDOM 方式从图鉴随机抽取，CHOOSE 方式按图鉴ID自选，支持领养多只
     */
    @Override
    public PetVO claimPet(PetClaimDTO dto) {
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

        // 根据图鉴数据初始化虚拟宠物，名字以用户取的为准，初始等级 1 级
        Pet pet = new Pet();
        pet.setUserId(dto.getUserId());
        pet.setType(PetType.VIRTUAL.name());
        pet.setName(dto.getName().trim());
        pet.setSpecies(catalog.getSpecies());
        pet.setBreed(catalog.getBreed());
        pet.setDescription(catalog.getDescription());
        pet.setImageUrl(catalog.getImageUrl());
        pet.setAge(1);
        pet.setLevel(1);
        pet.setExp(0L);
        pet.setSignStreak(0);
        // 虚拟宠物领养完成即自动签发身份卡
        pet.setCardIssueDate(LocalDate.now());
        save(pet);
        return toVO(pet);
    }

    /** 登记真实宠物：名称与种类必填，收养时间可选，纯档案不参与等级/经验/签到 */
    @Override
    public PetVO registerPet(PetRegisterDTO dto) {
        Pet pet = new Pet();
        pet.setUserId(dto.getUserId());
        pet.setType(PetType.REAL.name());
        pet.setName(dto.getName().trim());
        pet.setSpecies(dto.getSpecies().trim());
        pet.setAdoptionDate(dto.getAdoptionDate());
        // 真实宠物不参与游戏化，等级/经验/连续签到保持初始默认值
        pet.setAge(0);
        pet.setLevel(1);
        pet.setExp(0L);
        pet.setSignStreak(0);
        pet.setSterilized(0);
        save(pet);
        return toVO(pet);
    }

    /** 完善真实宠物档案：按 petId 定位并校验归属，仅真实宠物可完善 */
    @Override
    public PetVO updatePetProfile(PetProfileUpdateDTO dto) {
        Pet pet = getById(dto.getPetId());
        if (pet == null || !pet.getUserId().equals(dto.getUserId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        if (!PetType.REAL.name().equals(pet.getType())) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅真实宠物支持完善档案信息");
        }
        pet.setSpecies(dto.getSpecies());
        pet.setBreed(dto.getBreed());
        pet.setGender(dto.getGender());
        pet.setBirthday(dto.getBirthday());
        pet.setSterilized(dto.getSterilized());
        pet.setAdoptionDate(dto.getAdoptionDate());
        // 档案完善（种类/性别/生日齐备）后签发身份卡，仅首次签发不覆盖
        if (pet.getCardIssueDate() == null
                && StringUtils.hasText(pet.getSpecies())
                && pet.getGender() != null
                && pet.getBirthday() != null) {
            pet.setCardIssueDate(LocalDate.now());
        }
        updateById(pet);
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

    /** 更新宠物健康信息单项：按类别键定位字段，仅允许修改本人宠物 */
    @Override
    public PetVO updatePetHealth(PetHealthUpdateDTO dto) {
        Pet pet = getById(dto.getPetId());
        if (pet == null || !pet.getUserId().equals(dto.getUserId())) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        String value = dto.getValue() == null ? "" : dto.getValue().trim();
        switch (dto.getCategory()) {
            case "weight" -> pet.setWeight(value);
            case "bcs" -> pet.setBcs(value);
            case "deworming" -> pet.setDeworming(value);
            case "specialPeriod" -> pet.setSpecialPeriod(value);
            case "vaccine" -> pet.setVaccine(value);
            case "rearingMethod" -> pet.setRearingMethod(value);
            case "medicalHistory" -> pet.setMedicalHistory(value);
            default -> throw new BusinessException(ResultCode.BAD_REQUEST, "未知的健康信息类别：" + dto.getCategory());
        }
        updateById(pet);
        return toVO(pet);
    }

    /** 上传宠物头像：真实/虚拟宠物均可，校验归属与图片格式后存 OSS 并更新 imageUrl */
    @Override
    public PetVO uploadAvatar(Long petId, Long userId, MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "请选择要上传的图片");
        }
        if (file.getSize() > MAX_AVATAR_SIZE) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "头像图片不能超过 2MB");
        }
        String contentType = file.getContentType();
        if (contentType == null || !AVATAR_CONTENT_TYPES.contains(contentType.toLowerCase(Locale.ROOT))) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "仅支持 PNG / JPEG / WEBP 格式的图片");
        }
        Pet pet = getById(petId);
        if (pet == null || !pet.getUserId().equals(userId)) {
            throw new BusinessException(ResultCode.NOT_FOUND, "宠物不存在");
        }
        // 按日期分目录 + UUID 文件名，避免重名覆盖；与其他服务共用同一个桶
        String extension = getExtension(file.getOriginalFilename());
        String objectKey = "pet/avatar/" + LocalDate.now().format(AVATAR_DATE_FORMAT) + "/"
                + UUID.randomUUID().toString().replace("-", "") + "." + extension;
        pet.setImageUrl(ossService.upload(objectKey, file));
        updateById(pet);
        return toVO(pet);
    }

    /** 取文件扩展名（小写），无扩展名时用 jpg 兑底避免生成非法 objectKey */
    private String getExtension(String filename) {
        if (!StringUtils.hasText(filename)) {
            return "jpg";
        }
        int index = filename.lastIndexOf('.');
        if (index < 0 || index == filename.length() - 1) {
            return "jpg";
        }
        return filename.substring(index + 1).toLowerCase(Locale.ROOT);
    }

    /**
     * 每日签到
     * 每天限签一次，为用户所有虚拟宠物统一发放递增经验并累计连续天数
     * 真实宠物为纯档案，不参与签到
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public PetSignInVO signIn(PetSignInDTO dto) {
        List<Pet> pets = listVirtualPets(dto.getUserId());
        if (pets.isEmpty()) {
            throw new BusinessException(ResultCode.NOT_FOUND, "用户尚未领养虚拟宠物");
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
     * 发放给用户全部虚拟宠物，无虚拟宠物时返回 null，不阻断上游业务流程
     */
    @Override
    @Transactional(rollbackFor = Exception.class)
    public PetExpGainVO grantExp(PetExpGrantDTO dto) {
        // 解析经验来源，非法来源直接拒绝
        PetExpSource source;
        try {
            source = PetExpSource.valueOf(dto.getSource());
        } catch (IllegalArgumentException e) {
            throw new BusinessException(ResultCode.BAD_REQUEST, "未知的经验来源：" + dto.getSource());
        }
        // 经验仅发放给虚拟宠物，真实宠物为纯档案不参与；无虚拟宠物返回 null
        List<Pet> pets = listVirtualPets(dto.getUserId());
        if (pets.isEmpty()) {
            return null;
        }
        PetExpGainVO vo = null;
        for (Pet pet : pets) {
            int levelBefore = pet.getLevel() == null ? 1 : pet.getLevel();
            // 累加经验并处理升级，满级后不再累积
            PetLevelCalculator.gainExp(pet, source.getExp());
            updateById(pet);
            // 返回值仅内部使用（MQ 消费者忽略），以第一只虚拟宠物的结果为准
            if (vo == null) {
                vo = new PetExpGainVO();
                vo.setUserId(pet.getUserId());
                vo.setPetId(pet.getId());
                vo.setGainedExp(source.getExp());
                vo.setLevel(pet.getLevel());
                vo.setExp(pet.getExp());
                vo.setLeveledUp(pet.getLevel() > levelBefore);
            }
        }
        return vo;
    }

    /** 查询用户名下全部宠物，按领养先后排序 */
    private List<Pet> listByUserId(Long userId) {
        return list(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .orderByAsc(Pet::getCreateTime));
    }

    /** 查询用户名下全部虚拟宠物，按领养先后排序（签到与经验发放用） */
    private List<Pet> listVirtualPets(Long userId) {
        return list(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .eq(Pet::getType, PetType.VIRTUAL.name())
                .orderByAsc(Pet::getCreateTime));
    }

    /** 查询用户代表宠物：优先第一只虚拟宠物（AI聊天/他人主页用其画像），无则第一只真实宠物 */
    private Pet getRepresentPet(Long userId) {
        Pet virtual = getOne(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .eq(Pet::getType, PetType.VIRTUAL.name())
                .orderByAsc(Pet::getCreateTime)
                .last("limit 1"));
        if (virtual != null) {
            return virtual;
        }
        return getOne(new LambdaQueryWrapper<Pet>()
                .eq(Pet::getUserId, userId)
                .orderByAsc(Pet::getCreateTime)
                .last("limit 1"));
    }

    /** DO 转 VO，补充升级所需经验、类型名、性别名与绝育布尔值 */
    private PetVO toVO(Pet pet) {
        PetVO vo = new PetVO();
        BeanUtils.copyProperties(pet, vo);
        vo.setTypeName(PetType.labelOf(pet.getType()));
        vo.setGenderName(PetGender.labelOf(pet.getGender()));
        vo.setSterilized(pet.getSterilized() != null && pet.getSterilized() == 1);
        // 真实宠物的 age 列登记后不再维护（恒为 0），对外以生日实时换算的整岁为准
        // （与身份卡展示 / AI 上下文年龄口径一致，不足 1 岁为 0）
        if (PetType.REAL.name().equals(pet.getType()) && pet.getBirthday() != null) {
            vo.setAge(Math.max(0, Period.between(pet.getBirthday(), LocalDate.now()).getYears()));
        }
        if (pet.getLevel() == null || pet.getLevel() >= PetLevelCalculator.MAX_LEVEL) {
            vo.setNextLevelExp(0L);
        } else {
            vo.setNextLevelExp(PetLevelCalculator.expNeededForNextLevel(pet.getLevel()));
        }
        return vo;
    }
}
