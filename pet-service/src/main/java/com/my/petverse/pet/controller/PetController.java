package com.my.petverse.pet.controller;

import com.my.petverse.common.context.UserContext;
import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetProfileUpdateDTO;
import com.my.petverse.common.dto.pet.PetRegisterDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSignInDTO;
import com.my.petverse.common.dto.pet.PetUpdateDTO;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.result.Result;
import com.my.petverse.common.vo.pet.PetCatalogVO;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.pet.PetSignInVO;
import com.my.petverse.common.vo.pet.PetVO;
import com.my.petverse.pet.service.PetCatalogService;
import com.my.petverse.pet.service.PetService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 宠物接口控制器
 */
@RestController
@RequestMapping("/pet")
@RequiredArgsConstructor
public class PetController {

    private final PetService petService;

    private final PetCatalogService petCatalogService;

    /** 根据ID查询宠物 */
    @GetMapping("/{id}")
    public Result<PetVO> getById(@PathVariable("id") Long id) {
        return Result.success(petService.getPetById(id));
    }

    /** 查询宠物列表 */
    @GetMapping("/list")
    public Result<List<PetVO>> list() {
        return Result.success(petService.listPets());
    }

    /** 分页查询宠物 */
    @GetMapping("/page")
    public Result<PageResult<PetVO>> page(PetPageQueryDTO query) {
        return Result.success(petService.pagePets(query));
    }

    /** 获取宠物图鉴列表（新用户自选用） */
    @GetMapping("/catalog")
    public Result<List<PetCatalogVO>> catalog() {
        return Result.success(petCatalogService.listCatalog());
    }

    /** 随机抽取一个宠物图鉴（新用户抽卡预览） */
    @GetMapping("/catalog/random")
    public Result<PetCatalogVO> randomCatalog() {
        return Result.success(petCatalogService.randomCatalog());
    }

    /** 查询代表宠物（优先虚拟宠物）：不传 userId 时查当前登录用户，传 userId 用于用户主页展示他人宠物 */
    @GetMapping("/me")
    public Result<PetVO> me(@RequestParam(value = "userId", required = false) Long userId) {
        Long targetUserId = userId != null ? userId : UserContext.getUserId();
        return Result.success(petService.getMyPet(targetUserId));
    }

    /** 查询当前用户的全部宠物 */
    @GetMapping("/my-list")
    public Result<List<PetVO>> myList() {
        return Result.success(petService.listMyPets(UserContext.getUserId()));
    }

    /** 领取虚拟宠物（随机抽取或自选），支持领养多只，用户ID取自登录令牌 */
    @PostMapping("/claim")
    public Result<PetVO> claim(@RequestBody @Valid PetClaimDTO dto) {
        dto.setUserId(UserContext.getUserId());
        return Result.success(petService.claimPet(dto));
    }

    /** 登记真实宠物（名称 + 收养时间），用户ID取自登录令牌 */
    @PostMapping("/register")
    public Result<PetVO> register(@RequestBody @Valid PetRegisterDTO dto) {
        dto.setUserId(UserContext.getUserId());
        return Result.success(petService.registerPet(dto));
    }

    /** 完善真实宠物档案（种类/性别/生日/绝育），用户ID取自登录令牌 */
    @PutMapping("/profile")
    public Result<PetVO> updateProfile(@RequestBody @Valid PetProfileUpdateDTO dto) {
        dto.setUserId(UserContext.getUserId());
        return Result.success(petService.updatePetProfile(dto));
    }

    /** 修改宠物名称，用户ID取自登录令牌 */
    @PutMapping("/rename")
    public Result<PetVO> rename(@RequestBody @Valid PetRenameDTO dto) {
        dto.setUserId(UserContext.getUserId());
        return Result.success(petService.renamePet(dto));
    }

    /** 每日签到，为用户所有虚拟宠物发放经验值，用户ID取自登录令牌 */
    @PostMapping("/sign-in")
    public Result<PetSignInVO> signIn(@RequestBody @Valid PetSignInDTO dto) {
        dto.setUserId(UserContext.getUserId());
        return Result.success(petService.signIn(dto));
    }

    /** 按来源为用户所有虚拟宠物发放经验值（供动态发布事件消费者调用） */
    @PostMapping("/exp/grant")
    public Result<PetExpGainVO> grantExp(@RequestBody @Valid PetExpGrantDTO dto) {
        return Result.success(petService.grantExp(dto));
    }

    /** 新增宠物 */
    @PostMapping
    public Result<Long> save(@RequestBody @Valid PetSaveDTO dto) {
        return Result.success(petService.savePet(dto));
    }

    /** 修改宠物 */
    @PutMapping
    public Result<Boolean> update(@RequestBody @Valid PetUpdateDTO dto) {
        return Result.success(petService.updatePet(dto));
    }

    /** 删除宠物，仅允许删除本人宠物，用户ID取自登录令牌 */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id) {
        return Result.success(petService.deletePet(id, UserContext.getUserId()));
    }
}
