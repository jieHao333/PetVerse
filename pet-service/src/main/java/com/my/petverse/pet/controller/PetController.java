package com.my.petverse.pet.controller;

import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSetActiveDTO;
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

    /** 查询当前用户的出场宠物 */
    @GetMapping("/me")
    public Result<PetVO> me(@RequestParam("userId") Long userId) {
        return Result.success(petService.getMyPet(userId));
    }

    /** 查询当前用户的全部宠物 */
    @GetMapping("/my-list")
    public Result<List<PetVO>> myList(@RequestParam("userId") Long userId) {
        return Result.success(petService.listMyPets(userId));
    }

    /** 领取宠物（随机抽取或自选），支持领养多只 */
    @PostMapping("/claim")
    public Result<PetVO> claim(@RequestBody @Valid PetClaimDTO dto) {
        return Result.success(petService.claimPet(dto));
    }

    /** 修改宠物名称 */
    @PutMapping("/rename")
    public Result<PetVO> rename(@RequestBody @Valid PetRenameDTO dto) {
        return Result.success(petService.renamePet(dto));
    }

    /** 设置出场宠物 */
    @PutMapping("/active")
    public Result<PetVO> setActive(@RequestBody @Valid PetSetActiveDTO dto) {
        return Result.success(petService.setActivePet(dto));
    }

    /** 每日签到，为用户所有宠物发放经验值 */
    @PostMapping("/sign-in")
    public Result<PetSignInVO> signIn(@RequestBody @Valid PetSignInDTO dto) {
        return Result.success(petService.signIn(dto));
    }

    /** 按来源为出场宠物发放经验值（供其他服务调用，如发布动态奖励） */
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

    /** 删除宠物 */
    @DeleteMapping("/{id}")
    public Result<Boolean> delete(@PathVariable("id") Long id) {
        return Result.success(petService.deletePet(id));
    }
}
