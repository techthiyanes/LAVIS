from common.registry import registry

from models.blip_models import load_from_pretrained, init_tokenizer

from models.base_model import BaseModel
from models.med import XBertLMHeadDecoder
from models.vit import VisionTransformerEncoder


@registry.register_model("blip_caption")
class BlipCaption(BaseModel):
    def __init__(self, image_encoder, text_decoder, prompt=None, max_txt_len=40):
        super().__init__()

        self.tokenizer = init_tokenizer()

        self.visual_encoder = image_encoder
        self.text_decoder = text_decoder

        self.prompt = prompt
        self.prompt_length = len(self.tokenizer(self.prompt).input_ids) - 1

        self.max_txt_len = max_txt_len

    @classmethod
    def default_config_path(cls, model_type="base"):
        paths = {
            "base": "configs/models/blip_caption_base.yaml",
            "large": "configs/models/blip_caption_large.yaml",
        }

        assert model_type in paths, "Unknown model type {}".format(model_type)
        return paths[model_type]

    def forward_encoder(self, samples):
        image_embeds = self.visual_encoder(samples["image"])
        return image_embeds

    def forward_decoder(self, samples, image_embeds):
        # prepare inputs for forwarding decoder
        raw_text = samples["text_input"]
        text = self.tokenizer(
            raw_text,
            padding="longest",
            truncation=True,
            max_length=self.max_txt_len,
            return_tensors="pt",
        ).to(self.device)
        text.input_ids[:, 0] = self.tokenizer.bos_token_id

        # prepare targets for forwarding decoder
        decoder_targets = text.input_ids.masked_fill(
            text.input_ids == self.tokenizer.pad_token_id, -100
        )
        decoder_targets[:, : self.prompt_length] = -100

        _, decoder_output = self.text_decoder.forward_loss(
            text_tokenized=text,
            visual_embeds=image_embeds,
            decoder_targets=decoder_targets,
        )

        return {k: decoder_output[k] for k in decoder_output}

    def forward(self, samples):
        image_embeds = self.forward_encoder(samples)
        decoder_out = self.forward_decoder(samples, image_embeds)

        return decoder_out

    def generate(
        self,
        samples,
        use_nucleus_sampling=False,
        num_beams=3,
        max_length=30,
        min_length=10,
        top_p=0.9,
        repetition_penalty=1.0,
    ):
        # prepare inputs for decoder generation.
        encoder_out = self.forward_encoder(samples)
        image_embeds = encoder_out

        prompt = [self.prompt] * image_embeds.size(0)
        prompt = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        prompt.input_ids[:, 0] = self.tokenizer.bos_token_id
        prompt.input_ids = prompt.input_ids[:, :-1]

        # get decoded text
        decoder_out = self.text_decoder.generate_from_encoder(
            tokenized_prompt=prompt,
            visual_embeds=image_embeds,
            sep_token_id=self.tokenizer.sep_token_id,
            pad_token_id=self.tokenizer.pad_token_id,
            use_nucleus_sampling=use_nucleus_sampling,
            num_beams=num_beams,
            max_length=max_length,
            min_length=min_length,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
        )

        captions = []
        for output in decoder_out:
            caption = self.tokenizer.decode(output, skip_special_tokens=True)
            captions.append(caption[len(self.prompt) :])
        return captions

    @classmethod
    def _build_from_cfg(cls, cfg):
        # vision encoder
        image_encoder = VisionTransformerEncoder.build_from_cfg(cfg)
        # text encoder + multimodal decoder
        text_decoder = XBertLMHeadDecoder.build_from_cfg(cfg)

        prompt = cfg.get("prompt", None)
        max_txt_len = cfg.get("max_txt_len", 40)

        model = cls(image_encoder, text_decoder, prompt=prompt, max_txt_len=max_txt_len)

        # load pre-trained weights
        pretrain_path = cfg.get("pretrained", None)
        if pretrain_path is not None:
            model, msg = load_from_pretrained(model, url_or_filename=pretrain_path)

        return model