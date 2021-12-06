import torch
import torch.nn as nn
import torch.nn.functional as f
import pdb
import math
EPS = 1e-8

class MISO_1(nn.Module):
    def __init__(self,num_spks, num_ch, num_bottleneck,en_bottleneck_channels,de_bottleneck_channels,norm_type):
        super(MISO_1,self).__init__()
        #init#        
        # ch = 8 -> real + imag = 16
        # en_bottleneck_channels = [2*Ch,24,32,32,32,32,64,128,384]
        # de_bottleneck_channels = [384,128,64,32,32,32,32,24,2*Spk]

        en_bottleneck_channels.insert(0,2*num_ch)
        de_bottleneck_channels.append(2*num_spks)
        # block_length = len(en_bottleneck_channels)

        """
        num_bottleneck : number of bottleneck
        """
        self.num_bottleneck = num_bottleneck
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        for n_b in range(num_bottleneck):
            block = self.en_make_layer(n_b,en_bottleneck_channels[n_b], en_bottleneck_channels[n_b+1])
            self.encoders.append(block)
    
        # self.TCN = TemporalConvNet(2,7,384,384,384,norm_type)
        self.TCN = TemporalConvNet(2,7,128,128,128,norm_type)
        

        for n_b in range(num_bottleneck):
            block = self.de_make_layer(n_b,2*de_bottleneck_channels[n_b],de_bottleneck_channels[n_b+1])
            self.decoders.append(block)

        self.sigmoid = nn.Sigmoid()

    def en_make_layer(self,block_idx,in_channels, out_channels):
        layers = []
        if block_idx < 5:
            if block_idx == 0:
                layers.append(init_Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
            else:
                layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
        elif block_idx == 6:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
        else:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)
    
    def de_make_layer(self,block_idx,in_channels, out_channels):
        """
        in_channels : input + skip-connection 
        """
        layers = []
        if block_idx >= 2:
            if block_idx == 6:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(last_Deconv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1), padding=(1,0)))
            else:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
        elif block_idx == 0:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,1),padding=(1,0)))
        else:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)


    def forward(self,mixture):
        real_spec = mixture.real.float() # [B,C,T,F]
        imag_spec = mixture.imag.float() # [B,C,T,F]
        #reference mic -> circular shift 고려해야 됨.
        x = torch.cat((real_spec,imag_spec),dim=1)
        
        xs = []
        for i, encoder in enumerate(self.encoders):
            # print(i)    
            x = encoder(x)
            xs.append(x)
            # print(x.shape)
        #Reshape [B,384, T ,1] -> [B,384,T]
        x = torch.squeeze(x)

        #[B,384,T] -> [B,384,T]
        tcn_out = self.TCN(x)
        de_x =tcn_out
        #Reshape [B,384,T] -> [B,384,T,1]
        de_x = torch.unsqueeze(de_x,dim=-1)

        for i, decoder in enumerate(self.decoders):
            #[B,C,T,F] -> [B,2C,T,F]
            de_x = torch.cat((de_x, xs[self.num_bottleneck-1-i]), dim=1)
            de_x = decoder(de_x)

        #[B,2*Spks,T,257]
        B,Spk_realimag,T,F = de_x.size()
        #[B,2*Spks,T,257] -> [B,Spk,T,257]
        o_real_spec = de_x[:,0:Spk_realimag//2,:,:]
        o_imag_spec = de_x[:,Spk_realimag//2:Spk_realimag,:,:]
        #[B,Spk,T,257] -> [B,Spk,T,257]
        # separate = torch.complex(o_real_spec,o_imag_spec)
        if True in torch.isnan(o_real_spec) or True in torch.isnan(o_imag_spec):
            pdb.set_trace()
        return torch.complex(o_real_spec, o_imag_spec)

        # #Mask
        # separate_real = self.sigmoid(o_real_spec) 
        # separate_imag = self.sigmoid(o_imag_spec)
        # out_real_s1 = torch.unsqueeze(real_spec[:,0,:,:] * separate_real[:,0,:,:],dim=1)
        # out_real_s2 = torch.unsqueeze(real_spec[:,0,:,:] * separate_real[:,1,:,:],dim=1)
        # out_imag_s1 = torch.unsqueeze(imag_spec[:,0,:,:] * separate_imag[:,0,:,:],dim=1)
        # out_imag_s2 = torch.unsqueeze(imag_spec[:,0,:,:] * separate_imag[:,1,:,:],dim=1)
        # out_real = torch.cat((out_real_s1, out_real_s2), dim = 1)
        # out_imag = torch.cat((out_imag_s1, out_imag_s2), dim = 1)
        # separate = torch.complex(out_real,out_imag)
        # return separate



    # Mask Based     
    # def forward(self,mixture):
    #     real_spec = mixture.real.float() # [B,C,F,T]
    #     imag_spec = mixture.imag.float() # [B,C,F,T]
    #     #reference mic -> circular shift 고려해야 됨.
    #     x = torch.cat((real_spec,imag_spec),dim=1)
        
    #     xs = []
    #     for i, encoder in enumerate(self.encoders):
    #         # print(i)    
    #         x = encoder(x)
    #         xs.append(x)
    #     #Reshape [B,384, T ,1] -> [B,384,T]
    #     x = torch.squeeze(x)

    #     #[B,384,T] -> [B,384,T]
    #     tcn_out = self.TCN(x)
    #     de_x = x * self.sigmoid(tcn_out)

    #     #Reshape [B,384,T] -> [B,384,T,1]
    #     de_x = torch.unsqueeze(de_x,dim=-1)

    #     for i, decoder in enumerate(self.decoders):
    #         #[B,C,T,F] -> [B,2C,T,F]
    #         de_x = torch.cat((de_x, xs[self.num_bottleneck-1-i]), dim=1)
    #         de_x = decoder(de_x)

    #     #[B,2*Spks,T,257]
    #     B,Spk_realimag,T,F = de_x.size()
    #     #[B,2*Spks,T,257] -> [B,Spk,T,257]
    #     o_real_spec = de_x[:,0:Spk_realimag//2,:,:]
    #     o_imag_spec = de_x[:,Spk_realimag//2:Spk_realimag,:,:]
    #     #[B,Spk,T,257] -> [B,Spk,T,257]
    #     # separate = torch.complex(o_real_spec,o_imag_spec)
    #     if True in torch.isnan(o_real_spec) or True in torch.isnan(o_imag_spec):
    #         pdb.set_trace()
    #     return torch.complex(o_real_spec, o_imag_spec)


class MISO_2(nn.Module):
    def __init__(self,num_spks, num_ch, num_bottleneck,en_bottleneck_channels,de_bottleneck_channels,norm_type):
        super(MISO_2,self).__init__()
        #init#        
        # ch = 8 -> real + imag = 16
        # en_bottleneck_channels = [2*Ch,24,32,32,32,32,64,128,384]
        # de_bottleneck_channels = [384,128,64,32,32,32,32,24,2*Spk]
        en_bottleneck_channels.insert(0,2*(num_ch + 4))  # mixture 6ch + MISO1 1ch + BF 1ch
        de_bottleneck_channels.append(2*num_spks)
        # block_length = len(en_bottleneck_channels)

        """
        num_bottleneck : number of bottleneck
        """
        self.num_bottleneck = num_bottleneck
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        for n_b in range(num_bottleneck):
            block = self.en_make_layer(n_b,en_bottleneck_channels[n_b], en_bottleneck_channels[n_b+1])
            self.encoders.append(block)
    
        # self.TCN = TemporalConvNet(2,7,384,384,384,norm_type)
        self.TCN = TemporalConvNet(2,7,128,128,128,norm_type)
        

        for n_b in range(num_bottleneck):
            block = self.de_make_layer(n_b,2*de_bottleneck_channels[n_b],de_bottleneck_channels[n_b+1])
            self.decoders.append(block)

        self.sigmoid = nn.Sigmoid()

    def en_make_layer(self,block_idx,in_channels, out_channels):
        layers = []
        if block_idx < 5:
            if block_idx == 0:
                layers.append(init_Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
            else:
                layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
        elif block_idx == 6:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
        else:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)
    
    def de_make_layer(self,block_idx,in_channels, out_channels):
        """
        in_channels : input + skip-connection 
        """
        layers = []
        if block_idx >= 2:
            if block_idx == 6:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(last_Deconv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1), padding=(1,0)))
            else:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
        elif block_idx == 0:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,1),padding=(1,0)))
        else:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)


    def forward(self,mixture,MISO1,BF):
        mixture_real_spec = mixture.real.float() # [B,C,T,F]
        mixture_imag_spec = mixture.imag.float() # [B,C,T,F]

        MISO1_real_spec = MISO1.real.float()
        MISO1_imag_spec = MISO1.imag.float()

        BF_real_spec = BF.real.float()
        BF_imag_spec = BF.imag.float()

        real_spec = torch.cat((mixture_real_spec, MISO1_real_spec, BF_real_spec), dim= 1)
        imag_spec = torch.cat((mixture_imag_spec, MISO1_imag_spec, BF_imag_spec), dim= 1)

        #reference mic -> circular shift 고려해야 됨.
        x = torch.cat((real_spec,imag_spec),dim=1)
        
        xs = []
        for i, encoder in enumerate(self.encoders):
            # print(i)    
            x = encoder(x)
            xs.append(x)
            # print(x.shape)
        #Reshape [B,384, T ,1] -> [B,384,T]
        x = torch.squeeze(x)

        #[B,384,T] -> [B,384,T]
        tcn_out = self.TCN(x)
        de_x =tcn_out
        #Reshape [B,384,T] -> [B,384,T,1]
        de_x = torch.unsqueeze(de_x,dim=-1)

        for i, decoder in enumerate(self.decoders):
            #[B,C,T,F] -> [B,2C,T,F]
            de_x = torch.cat((de_x, xs[self.num_bottleneck-1-i]), dim=1)
            de_x = decoder(de_x)

        #[B,2*Spks,T,257]
        B,Spk_realimag,T,F = de_x.size()
        #[B,2*Spks,T,257] -> [B,Spk,T,257]
        o_real_spec = de_x[:,0:Spk_realimag//2,:,:]
        o_imag_spec = de_x[:,Spk_realimag//2:Spk_realimag,:,:]
        #[B,Spk,T,257] -> [B,Spk,T,257]
        # separate = torch.complex(o_real_spec,o_imag_spec)
        if True in torch.isnan(o_real_spec) or True in torch.isnan(o_imag_spec):
            pdb.set_trace()
        return torch.complex(o_real_spec, o_imag_spec)



class MISO_3(nn.Module):
    def __init__(self,num_spks, num_ch, num_bottleneck,en_bottleneck_channels,de_bottleneck_channels,norm_type):
        super(MISO_3,self).__init__()
        #init#        
        # ch = 8 -> real + imag = 16
        # en_bottleneck_channels = [2*Ch,24,32,32,32,32,64,128,384]
        # de_bottleneck_channels = [384,128,64,32,32,32,32,24,2*Spk]
    
        en_bottleneck_channels.insert(0,2*(num_ch + 2))  # mixture 6ch + MISO1 1ch + BF 1ch
        de_bottleneck_channels.append(2*num_spks)
        # block_length = len(en_bottleneck_channels)

        """
        num_bottleneck : number of bottleneck
        """
        self.num_bottleneck = num_bottleneck
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        for n_b in range(num_bottleneck):
            block = self.en_make_layer(n_b,en_bottleneck_channels[n_b], en_bottleneck_channels[n_b+1])
            self.encoders.append(block)
    
        # self.TCN = TemporalConvNet(2,7,384,384,384,norm_type)
        self.TCN = TemporalConvNet(2,7,128,128,128,norm_type)
        

        for n_b in range(num_bottleneck):
            block = self.de_make_layer(n_b,2*de_bottleneck_channels[n_b],de_bottleneck_channels[n_b+1])
            self.decoders.append(block)

        self.sigmoid = nn.Sigmoid()

    def en_make_layer(self,block_idx,in_channels, out_channels):
        layers = []
        if block_idx < 5:
            if block_idx == 0:
                layers.append(init_Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
            else:
                layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
                layers.append(DenseBlock(out_channels,out_channels,out_channels))
        elif block_idx == 6:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1),padding=(1,0)))
        else:
            layers.append(Conv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)
    
    def de_make_layer(self,block_idx,in_channels, out_channels):
        """
        in_channels : input + skip-connection 
        """
        layers = []
        if block_idx >= 2:
            if block_idx == 6:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(last_Deconv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,1), padding=(1,0)))
            else:
                layers.append(DenseBlock(in_channels,in_channels//2,in_channels))
                layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3), stride=(1,2),padding=(1,0)))
        elif block_idx == 0:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,1),padding=(1,0)))
        else:
            layers.append(DeConv2d_(in_channels,out_channels,kernel_size=(3,3),stride=(1,2),padding=(1,0)))

        return nn.Sequential(*layers)


    def forward(self,mixture,MISO1,BF):
        mixture_real_spec = mixture.real.float() # [B,C,T,F]
        mixture_imag_spec = mixture.imag.float() # [B,C,T,F]

        MISO1_real_spec = MISO1.real.float()
        MISO1_imag_spec = MISO1.imag.float()

        BF_real_spec = BF.real.float()
        BF_imag_spec = BF.imag.float()

        real_spec = torch.cat((mixture_real_spec, MISO1_real_spec, BF_real_spec), dim= 1)
        imag_spec = torch.cat((mixture_imag_spec, MISO1_imag_spec, BF_imag_spec), dim= 1)

        #reference mic -> circular shift 고려해야 됨.
        x = torch.cat((real_spec,imag_spec),dim=1)
        
        xs = []
        for i, encoder in enumerate(self.encoders):
            # print(i)    
            x = encoder(x)
            xs.append(x)
            # print(x.shape)
        #Reshape [B,384, T ,1] -> [B,384,T]
        x = torch.squeeze(x)

        #[B,384,T] -> [B,384,T]
        tcn_out = self.TCN(x)
        de_x =tcn_out
        #Reshape [B,384,T] -> [B,384,T,1]
        de_x = torch.unsqueeze(de_x,dim=-1)

        for i, decoder in enumerate(self.decoders):
            #[B,C,T,F] -> [B,2C,T,F]
            de_x = torch.cat((de_x, xs[self.num_bottleneck-1-i]), dim=1)
            de_x = decoder(de_x)

        #[B,2*Spks,T,257]
        B,Spk_realimag,T,F = de_x.size()
        #[B,2*Spks,T,257] -> [B,Spk,T,257]
        o_real_spec = de_x[:,0:Spk_realimag//2,:,:]
        o_imag_spec = de_x[:,Spk_realimag//2:Spk_realimag,:,:]
        #[B,Spk,T,257] -> [B,Spk,T,257]
        # separate = torch.complex(o_real_spec,o_imag_spec)
        if True in torch.isnan(o_real_spec) or True in torch.isnan(o_imag_spec):
            pdb.set_trace()
        return torch.complex(o_real_spec, o_imag_spec)





class init_Conv2d_(nn.Module):
    def __init__(self,in_channels, out_channels, kernel_size=(3,3),stride=(1,1),padding=(1,0)):
        super(init_Conv2d_, self).__init__()
        self.conv2d = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size,stride=stride, padding=padding)
    def forward(self,x):
        return self.conv2d(x)

class Conv2d_(nn.Module):
    def __init__(self,in_channels, out_channels, kernel_size=(3,3),stride=(1,2),padding=(1,0), norm_type="IN"):
        super(Conv2d_,self).__init__()
        conv2d = nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding)
        elu = nn.ELU()
        norm = nn.InstanceNorm2d(out_channels,affine=False) # 384
        self.net = nn.Sequential(conv2d,elu,norm)
    def forward(self,x):
        return self.net(x)

class last_Deconv2d_(nn.Module):
    def __init__(self,in_channels,out_channels, kernel_size=(3,3), stride=(1,1), padding=(1,0)):
        super(last_Deconv2d_,self).__init__()
        self.deconv2d = nn.ConvTranspose2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride, padding=padding)
    def forward(self,x):
        return self.deconv2d(x)

class DeConv2d_(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride, padding, norm_type="IN"):
        super(DeConv2d_,self).__init__()
        deconv2d = nn.ConvTranspose2d(in_channels,out_channels,kernel_size=kernel_size, stride=stride, padding=padding)
        elu = nn.ELU()
        norm = nn.InstanceNorm2d(out_channels,affine=False)
        self.net = nn.Sequential(deconv2d,elu,norm)
    def forward(self,x):
        return self.net(x)



class DenseBlock(nn.Module):

    def __init__(self,init_ch, g1, g2):
        super(DenseBlock,self).__init__()

        self.conv1 = nn.Sequential(
            nn.Conv2d(init_ch,g1, kernel_size=(3,3),stride=(1,1),padding=(1,1)),
            nn.ELU(),
            nn.InstanceNorm2d(g1,affine=False)
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(init_ch+g1,g1, kernel_size=(3,3),stride=(1,1),padding=(1,1)),
            nn.ELU(),
            nn.InstanceNorm2d(g1,affine=False)
        )
        self.conv3 = nn.Sequential(
            nn.Conv2d(init_ch+2*g1,g1, kernel_size=(3,3),stride=(1,1),padding=(1,1)),
            nn.ELU(),
            nn.InstanceNorm2d(g1,affine=False)
        )        
        self.conv4 = nn.Sequential(
            nn.Conv2d(init_ch+3*g1,g1, kernel_size=(3,3),stride=(1,1),padding=(1,1)),
            nn.ELU(),
            nn.InstanceNorm2d(g1,affine=False)
        )
        self.conv5 = nn.Sequential(
            nn.Conv2d(init_ch+4*g1,g2, kernel_size=(3,3),stride=(1,1),padding=(1,1)),
            nn.ELU(),
            nn.InstanceNorm2d(g2,affine=False)
        )
    def forward(self,x):
        y0 = self.conv1(x)
        
        y0_x = torch.cat((x,y0),dim=1)
        y1 = self.conv2(y0_x)

        y1_0_x = torch.cat((x,y0,y1),dim=1)
        y2 = self.conv3(y1_0_x)

        y2_1_0_x = torch.cat((x,y0,y1,y2),dim=1)
        y3 = self.conv4(y2_1_0_x)

        y3_2_1_0_x = torch.cat((x,y0,y1,y2,y3),dim=1)
        y4 = self.conv5(y3_2_1_0_x)
        
        return y4



class TemporalConvNet(nn.Module):
    def __init__(self, R, X, C_in, C_hidden, C_out, norm_type = "IN"):
        """
        R : Number of repeats  R = 2
        X : Number of convolutional blocks in each repeat X = 7
        C_in : Number of channels in input
        C_hidden : Number of channels in first conv block output
        C_out : Number of channels in output
        """
        super(TemporalConvNet,self).__init__()
        
        repeats = []
        for r in range(R):
            blocks = []
            for x in range(X):
                dilation = 2**x  # 0,2,4,8,16,32,64
                # kernel(P) 3 stride 1 padding d dilation d featuremap 384
                padding = 2**x
                blocks += [TemporalBlock(C_in,C_hidden,C_out,
                                         kernel_size= 3, stride = 1, padding=padding, dilation=dilation,
                                         norm_type = norm_type)]
            repeats += [nn.Sequential(*blocks)]
        self.temporal_conv_net = nn.Sequential(*repeats)
        
    def forward(self,x):
        """
        Input : [B,C,T] 
        Output : [B,C,T]
        """
        return self.temporal_conv_net(x)

class TemporalBlock(nn.Module):
    def __init__(self,in_channels,hidden_channels,out_channels,kernel_size,
                 stride,padding,dilation,norm_type="IN"):
        """
        in_channels : 384
        out_channels : 384
        kernel_size : 3
        stride : 1
        padding : d
        dilation : d
        featuremap : 384
        """
        super(TemporalBlock,self).__init__()
        norm_1 = chose_norm(norm_type, in_channels) # 384
        elu_1 = nn.ELU()
        # [B,C,T] -> [B,C,T]
        dsconv_1 = DepthwiseSeparableConv(in_channels,hidden_channels,kernel_size,stride,padding,dilation,norm_type="gLN")
        
        norm_2 = chose_norm(norm_type, hidden_channels) # 384
        elu_2 = nn.ELU()
        dsconv_2 = DepthwiseSeparableConv(hidden_channels,out_channels,kernel_size,stride,padding,dilation,norm_type="gLN")
        
        self.net = nn.Sequential(norm_1, elu_1, dsconv_1, norm_2, elu_2, dsconv_2)

    def forward(self,x):
        """
        Input : [B,C,T]
        Output : [B,C,T]
        """
        if x.dim() == 2:
            x = torch.unsqueeze(x,dim=0)
        residual = x
        out = self.net(x)
        return out + residual


class DepthwiseSeparableConv(nn.Module):
    def __init__(self,in_channels,out_channels,kernel_size,stride,padding,dilation,norm_type="gLN"):
        super(DepthwiseSeparableConv,self).__init__()
        depthwise_conv = nn.Conv1d(in_channels,in_channels,kernel_size,stride=stride,
                                   padding=padding,dilation=dilation,groups=in_channels,bias=False)
        prelu = nn.PReLU()
        norm = chose_norm(norm_type,in_channels)
        pointwise_conv = nn.Conv1d(in_channels,out_channels,1,bias=False)
        self.net = nn.Sequential(depthwise_conv, prelu, norm, pointwise_conv)
    def forward(self,x):
        """
        Input : [B,C_in,T]
        output : [B,C_out,T]
        """
        return self.net(x)


def chose_norm(norm_type, channel_size):
    """
    input : [B, C, T]
    """
    if norm_type=="gLN":
        return GlobalLayerNorm(channel_size)
    elif norm_type == "cLN":
        return ChannelwiseLayerNorm(channel_size)
    elif norm_type == "IN":
        return nn.InstanceNorm1d(channel_size,affine=False)
    else:
        return nn.BatchNorm1d(channel_size)

class ChannelwiseLayerNorm(nn.Module):
    """Channel-wise Layer Normalization (cLN)"""
    def __init__(self, channel_size):
        super(ChannelwiseLayerNorm, self).__init__()
        self.gamma = nn.Parameter(torch.Tensor(1, channel_size, 1))  # [1, N, 1]
        self.beta = nn.Parameter(torch.Tensor(1, channel_size,1 ))  # [1, N, 1]
        self.reset_parameters()

    def reset_parameters(self):
        self.gamma.data.fill_(1)
        self.beta.data.zero_()

    def forward(self, y):
        """
        Args:
            y: [M, N, K], M is batch size, N is channel size, K is length
        Returns:
            cLN_y: [M, N, K]
        """
        mean = torch.mean(y, dim=1, keepdim=True)  # [M, 1, K]
        var = torch.var(y, dim=1, keepdim=True, unbiased=False)  # [M, 1, K]
        cLN_y = self.gamma * (y - mean) / torch.pow(var + EPS, 0.5) + self.beta
        return cLN_y



class GlobalLayerNorm(nn.Module):
    """Global Layer Normalization (gLN)"""
    def __init__(self, channel_size):
        super(GlobalLayerNorm, self).__init__()
        self.gamma = nn.Parameter(torch.Tensor(1, channel_size, 1))  # [1, N, 1]
        self.beta = nn.Parameter(torch.Tensor(1, channel_size,1 ))  # [1, N, 1]
        self.reset_parameters()

    def reset_parameters(self):
        self.gamma.data.fill_(1)
        self.beta.data.zero_()

    def forward(self, y):
        """
        Args:
            y: [M, N, K], M is batch size, N is channel size, K is length
        Returns:
            gLN_y: [M, N, K]
        """
        # TODO: in torch 1.0, torch.mean() support dim list
        mean = y.mean(dim=1, keepdim=True).mean(dim=2, keepdim=True) #[M, 1, 1]
        var = (torch.pow(y-mean, 2)).mean(dim=1, keepdim=True).mean(dim=2, keepdim=True)
        gLN_y = self.gamma * (y - mean) / torch.pow(var + EPS, 0.5) + self.beta
        return gLN_y


if __name__ == "__main__":
    
    input = torch.randn(10,8,150,257, dtype=torch.cfloat)
    model = MISO_1(8,8,2,"IN")    
    output = model(input)
    pdb.set_trace()
