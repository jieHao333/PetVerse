package com.my.petverse.pet.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.my.petverse.common.entity.pet.PetCatalog;
import com.my.petverse.common.enums.PetRarity;
import com.my.petverse.common.exception.BusinessException;
import com.my.petverse.common.result.ResultCode;
import com.my.petverse.common.vo.pet.PetCatalogVO;
import com.my.petverse.pet.mapper.PetCatalogMapper;
import com.my.petverse.pet.service.PetCatalogService;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.concurrent.ThreadLocalRandom;
import java.util.stream.Collectors;

/**
 * 宠物图鉴服务实现类
 */
@Service
public class PetCatalogServiceImpl extends ServiceImpl<PetCatalogMapper, PetCatalog> implements PetCatalogService {

    /** 宠物图鉴列表 */
    @Override
    public List<PetCatalogVO> listCatalog() {
        return list().stream().map(this::toVO).collect(Collectors.toList());
    }

    /** 按稀有度加权随机抽取一个图鉴宠物（普通60%/稀有30%/传说10%） */
    @Override
    public PetCatalog randomCatalogEntity() {
        PetRarity rarity = randomRarity();
        List<PetCatalog> pool = list(new LambdaQueryWrapper<PetCatalog>()
                .eq(PetCatalog::getRarity, rarity.getCode()));
        // 当前稀有度无数据时退回全量图鉴
        if (pool.isEmpty()) {
            pool = list();
        }
        if (pool.isEmpty()) {
            throw new BusinessException(ResultCode.INTERNAL_ERROR, "宠物图鉴为空");
        }
        return pool.get(ThreadLocalRandom.current().nextInt(pool.size()));
    }

    /** 随机抽取预览（返回 VO） */
    @Override
    public PetCatalogVO randomCatalog() {
        return toVO(randomCatalogEntity());
    }

    /**
     * 随机生成稀有度
     * 概率：普通 60%、稀有 30%、传说 10%
     */
    private PetRarity randomRarity() {
        int value = ThreadLocalRandom.current().nextInt(100);
        if (value < 10) {
            return PetRarity.LEGENDARY;
        }
        if (value < 40) {
            return PetRarity.RARE;
        }
        return PetRarity.COMMON;
    }

    /** DO 转 VO，补充稀有度名称 */
    private PetCatalogVO toVO(PetCatalog catalog) {
        PetCatalogVO vo = new PetCatalogVO();
        BeanUtils.copyProperties(catalog, vo);
        vo.setRarityName(PetRarity.of(catalog.getRarity()).getDesc());
        return vo;
    }
}
