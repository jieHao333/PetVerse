package com.my.petverse.pet.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.dto.pet.PetClaimDTO;
import com.my.petverse.common.dto.pet.PetExpGrantDTO;
import com.my.petverse.common.dto.pet.PetPageQueryDTO;
import com.my.petverse.common.dto.pet.PetRenameDTO;
import com.my.petverse.common.dto.pet.PetSaveDTO;
import com.my.petverse.common.dto.pet.PetSignInDTO;
import com.my.petverse.common.dto.pet.PetUpdateDTO;
import com.my.petverse.common.entity.pet.Pet;
import com.my.petverse.common.result.PageResult;
import com.my.petverse.common.vo.pet.PetExpGainVO;
import com.my.petverse.common.vo.pet.PetSignInVO;
import com.my.petverse.common.vo.pet.PetVO;

import java.util.List;

/**
 * 宠物服务接口
 */
public interface PetService extends IService<Pet> {

    /** 根据ID查询宠物 */
    PetVO getPetById(Long id);

    /** 查询宠物列表 */
    List<PetVO> listPets();

    /** 分页查询宠物 */
    PageResult<PetVO> pagePets(PetPageQueryDTO query);

    /** 新增宠物，返回宠物ID */
    Long savePet(PetSaveDTO dto);

    /** 修改宠物 */
    boolean updatePet(PetUpdateDTO dto);

    /** 删除宠物 */
    boolean deletePet(Long id);

    /** 查询当前用户的宠物 */
    PetVO getMyPet(Long userId);

    /** 新用户领取宠物（随机抽取或自选） */
    PetVO claimPet(PetClaimDTO dto);

    /** 修改宠物名称 */
    PetVO renamePet(PetRenameDTO dto);

    /** 宠物每日签到获取经验值 */
    PetSignInVO signIn(PetSignInDTO dto);

    /** 按来源为宠物发放经验值（如发布笔记），用户无宠物时返回 null */
    PetExpGainVO grantExp(PetExpGrantDTO dto);
}
