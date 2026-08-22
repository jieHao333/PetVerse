package com.my.petverse.pet.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.my.petverse.common.entity.pet.PetCatalog;
import com.my.petverse.common.vo.pet.PetCatalogVO;

import java.util.List;

/**
 * 宠物图鉴服务接口
 */
public interface PetCatalogService extends IService<PetCatalog> {

    /** 宠物图鉴列表 */
    List<PetCatalogVO> listCatalog();

    /** 随机抽取一个图鉴宠物（返回实体） */
    PetCatalog randomCatalogEntity();

    /** 随机抽取预览（返回 VO） */
    PetCatalogVO randomCatalog();
}
